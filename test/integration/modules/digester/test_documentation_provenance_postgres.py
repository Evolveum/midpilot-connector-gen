# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Protocol JSON -> real extraction/ranking -> job persistence -> HTTP source content.

Only LLM responses and progress reporting are stubbed; reference construction,
content-type selection, ranking assembly, persistence and retrieval remain real.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app import create_api
from src.auth.context import AuthContext, AuthMode
from src.auth.dependencies import authenticate_request
from src.config import config
from src.core.db import get_db
from src.database.models import Base, Session
from src.database.repositories.documentation_repository import DocumentationRepository
from src.database.repositories.session_repository import SessionRepository
from src.jobs.session_persistence import persist_result_to_session
from src.modules.digester.enums import ConfidenceLevel
from src.modules.digester.extractors.scim.endpoints import pregenerate_scim_endpoints
from src.modules.digester.extractors.scim.object_class import extract_scim_object_classes
from src.modules.digester.extractors.sql.object_class import extract_sql_object_classes


@pytest_asyncio.fixture()
async def protocol_store():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL provenance tests")
    schema = f"test_documentation_provenance_{uuid4().hex}"
    engine = create_async_engine(database_url, execution_options={"schema_translate_map": {None: schema}})
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session_id = uuid4()
        async with factory() as db:
            db.add(Session(session_id=session_id))
            await db.commit()

        async def database():
            async with factory() as db:
                yield db

        async def authenticate(request: Request):
            request.state.auth = AuthContext(AuthMode.master)

        app = create_api()
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[authenticate_request] = authenticate
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            with (
                patch("src.modules.digester.extractors.scim.baseline.async_session_maker", factory),
                patch("src.jobs.session_persistence.async_session_maker", factory),
            ):
                yield SimpleNamespace(factory=factory, session_id=session_id, client=client)
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.fixture()
def ranking_llm():
    ranking = "src.modules.digester.aggregation.object_class_ranking"
    confidence = SimpleNamespace(
        objectClasses=[
            SimpleNamespace(name=name, confidence=ConfidenceLevel.MEDIUM)
            for name in ["Group", "User", "GroupMembers", "EnterpriseGroup"]
        ]
    )
    with (
        patch(f"{ranking}.get_default_llm", return_value=MagicMock()),
        patch(f"{ranking}.make_basic_chain", return_value=MagicMock()),
        patch(f"{ranking}.invoke_llm", AsyncMock(return_value=confidence)) as llm,
        patch(f"{ranking}.update_job_progress", AsyncMock()),
        patch(f"{ranking}.append_job_error", AsyncMock()) as errors,
        patch("src.modules.digester.extractors.sql.object_class.update_job_progress", AsyncMock()),
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", AsyncMock()),
        patch("src.modules.digester.extraction.llm_execution.update_job_progress", AsyncMock()),
        patch("src.modules.digester.extractors.scim.endpoints.update_job_progress", AsyncMock()),
        patch("src.modules.digester.extractors.scim.endpoints.increment_processed_documents", AsyncMock()),
    ):
        yield llm
        errors.assert_not_awaited()


async def upload(store, payload, content_type, filename):
    content = json.dumps(payload)
    doc_id = uuid4()
    async with store.factory() as db:
        chunk_id = await DocumentationRepository(db).create_documentation_item(
            session_id=store.session_id,
            doc_id=doc_id,
            source="upload",
            content=content,
            metadata={"content_type": content_type, "filename": filename, "chunk_number": 1},
        )
        await db.commit()
    return {"docId": str(doc_id), "chunkId": str(chunk_id), "content": content, "contentType": content_type}


async def persist(store, result, key="objectClassesOutput"):
    job_id = uuid4()
    async with store.factory() as db:
        await SessionRepository(db).update_session(store.session_id, {f"{key[:-6]}JobId": str(job_id)})
        await db.commit()
    assert await persist_result_to_session(
        job_id=job_id,
        session_id=store.session_id,
        session_result_key=key,
        result_dict=result,
    )


async def panel(store, name, expected, **params):
    response = await store.client.get(
        f"{config.app.api_base_url}/v1/digester/{store.session_id}/classes/{name}/documentation",
        params=params,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nextOffset"] is None
    actual = {item["chunkId"]: item for item in body["items"]}
    assert set(actual) == {item["chunkId"] for item in expected}
    for item in expected:
        assert actual[item["chunkId"]]["content"] == item["content"]
        assert actual[item["chunkId"]]["docId"] == item["docId"]
        assert actual[item["chunkId"]]["contentType"] == item["contentType"]
        assert actual[item["chunkId"]]["source"] == "upload"
    return body


@pytest.mark.parametrize(
    "content_type,shape",
    [
        ("application/json", "raw"),
        ("application/conndev+json", "table"),
        (" APPLICATION/COM.EVOLVEUM.CONNDEV+JSON; charset=utf-8 ", "table"),
        ("application/conndev+json", "object_class"),
        ("application/com.evolveum.conndev+json", "object_class"),
    ],
)
@pytest.mark.asyncio
async def test_sql_json_references_survive_ranking_persistence_and_http(
    protocol_store, ranking_llm, content_type, shape
):
    store = protocol_store

    def schema(name):
        table = {"name": name, "schema": "public", "columns": [{"name": "id", "type": "integer"}]}
        if shape == "raw":
            return {"tables": [table]}
        if shape == "object_class":
            return {
                "name": name[:-1].title(),
                "uid": name[:-1].title(),
                "sql": {
                    "type": "c:ShadowType",
                    "object": {"objectClass": "ri:conndev_sql", "attributes": {"table": name, "schema": "public"}},
                },
                "attributes": [
                    {
                        "type": "c:ShadowType",
                        "object": {
                            "objectClass": "ri:conndev_Attribute",
                            "attributes": {"name": "id", "connId": {"type": "integer"}, "sql": {"path": "id"}},
                        },
                    }
                ],
            }
        table["tableType"] = "TABLE"
        return {"tableContent": json.dumps(table), "name": name, "schema": "public", "uid": name, "tableType": "TABLE"}

    group = await upload(store, schema("groups"), content_type, "groups.json")
    await upload(store, schema("users"), content_type, "users.json")
    async with store.factory() as db:
        docs = await DocumentationRepository(db).get_documentation_items_by_session(store.session_id)
    result = await extract_sql_object_classes(docs, uuid4())
    ranking_llm.assert_awaited_once()
    await persist(store, result)
    body = await panel(store, "GROUP", [group])
    assert body["objectClass"] == "Group"
    assert body["items"][0]["filename"] == "groups.json"


@pytest.mark.parametrize(
    "content_type", ["application/conndev+json", " APPLICATION/COM.EVOLVEUM.CONNDEV+JSON; charset=utf-8 "]
)
@pytest.mark.asyncio
async def test_scim_schema_extension_embedded_class_and_endpoint_sources(protocol_store, ranking_llm, content_type):
    store = protocol_store
    group_schema = {
        "id": "urn:ietf:params:scim:schemas:core:2.0:Group",
        "name": "Group",
        "attributes": [
            {
                "name": "members",
                "type": "complex",
                "multiValued": True,
                "subAttributes": [{"name": "value", "type": "string"}],
            }
        ],
    }
    extension_schema = {
        "id": "urn:example:params:scim:schemas:extension:enterprise:2.0:Group",
        "name": "EnterpriseGroup",
        "attributes": [{"name": "department", "type": "string"}],
    }

    def schema_document(schema):
        return {"name": schema["name"], "id": schema["id"], "schemaContent": json.dumps(schema)}

    group = await upload(store, schema_document(group_schema), content_type, "group.json")
    extension = await upload(store, schema_document(extension_schema), content_type, "extension.json")
    resource = await upload(
        store,
        {
            "schema": group_schema["id"],
            "primarySchema": json.dumps(group_schema),
            "endpoint": "/Groups",
            "name": "Group",
            "id": "Group",
            "schemaExtensions": json.dumps([extension_schema]),
        },
        content_type,
        "resource.json",
    )
    await upload(
        store,
        schema_document({"id": "urn:ietf:params:scim:schemas:core:2.0:User", "name": "User", "attributes": []}),
        content_type,
        "user.json",
    )
    # Same recognizable schema shape, but wrong media type: it must not become baseline evidence.
    await upload(store, schema_document(group_schema), "application/json", "not-a-baseline.json")
    async with store.factory() as db:
        docs = await DocumentationRepository(db).get_conndev_documentation_items_by_session(store.session_id)
    assert len(docs) == 4
    result = await extract_scim_object_classes(docs, uuid4(), store.session_id)
    ranking_llm.assert_awaited_once()
    await persist(store, result)
    await panel(store, "Group", [group, extension, resource])
    await panel(store, "GroupMembers", [group, extension, resource])
    await panel(store, "EnterpriseGroup", [extension, resource])

    endpoints = await pregenerate_scim_endpoints(session_id=store.session_id, object_class="Group", job_id=uuid4())
    assert endpoints is not None
    await persist(store, endpoints, "groupEndpointsOutput")
    body = await panel(store, "Group", [resource], method="POST", path="/Groups")
    assert body["endpoint"] == {"method": "POST", "path": "/Groups"}
    # Reads did not trigger any new LLM calls.
    ranking_llm.assert_awaited_once()
