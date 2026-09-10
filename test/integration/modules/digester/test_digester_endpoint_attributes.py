# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester class-attributes endpoints."""

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.app import create_api
from src.auth.context import AuthContext, AuthMode
from src.auth.dependencies import authenticate_request
from src.config import config
from src.core.db import get_db
from src.jobs import job_input_reference
from src.jobs.schema import JobStatusMultiDocResponse
from src.modules.digester.routes.attributes import (
    extract_class_attributes,
    get_class_attributes_status,
    override_class_attributes,
)
from src.modules.digester.schemas import AttributeInfoScim, AttributeResponse
from src.shared.enums import JobStatus

SQL_CONTEXT = {
    "physicalTable": {
        "databaseCatalog": "connector_project_db",
        "databaseSchema": "public",
        "table": "app_user",
        "tableType": "TABLE",
    },
    "connectorObjectClass": {
        "name": "app_user",
        "attributes": [
            {"name": "__NAME__", "connIdType": "string", "column": None},
            {"name": "login", "connIdType": "string", "column": "username"},
        ],
    },
}


# CLASS ATTRIBUTES
@pytest.mark.asyncio
async def test_extract_class_attributes_success():
    """Test successful extraction of class attributes."""
    session_id = uuid4()
    job_id = uuid4()
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    fake_docs = [{"chunkId": chunk_id, "docId": doc_id, "content": "fake content for testing"}]

    # Mock objectClassesOutput with relevant chunks for the User class
    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "user",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantDocumentations": [
                    {"docId": doc_id, "chunkId": chunk_id},
                    {"docId": doc_id, "chunkId": chunk_id},
                ],
                "endpoints": [],
                "attributes": {},
            }
        ]
    }

    # extract_class_attributes reads objectClassesOutput and documentationItems
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=mock_object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.routes.attributes.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new=AsyncMock(return_value=[{"docId": doc_id, "chunkId": chunk_id}]),
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new=AsyncMock(return_value=fake_docs),
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_attributes(
            session_id=session_id,
            object_class="User",
            db=MagicMock(),
            api_type=None,
        )

        assert response.jobId == job_id
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")
        mock_schedule.assert_awaited_once()
        schedule_kwargs = mock_schedule.call_args.kwargs
        assert schedule_kwargs["input_payload"]["objectClass"] == "user"
        assert schedule_kwargs["worker_args"][0] == job_input_reference("documentationItems")
        assert schedule_kwargs["worker_args"][1] == "user"
        assert schedule_kwargs["worker_args"][3] == job_input_reference("relevantDocumentations")
        assert schedule_kwargs["session_result_key"] == "userAttributesOutput"
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_class_attributes_scim_allows_missing_relevant_chunks():
    session_id = uuid4()
    job_id = uuid4()
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "UserPhoneNumbers",
                "relevant": "true",
                "superclass": "User",
                "abstract": False,
                "embedded": True,
                "description": "Phone numbers for the User.",
            }
        ]
    }

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=mock_object_classes_output)
    mock_repo.update_session = AsyncMock()
    mock_relevance_repo = MagicMock()
    mock_relevance_repo.get_relevant_chunks_grouped_by_entity = AsyncMock(
        return_value={"user": [{"docId": doc_id, "chunkId": chunk_id}]}
    )

    with (
        patch("src.modules.digester.routes.attributes.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.selection.documentation_selector.RelevantChunkRepository",
            return_value=mock_relevance_repo,
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=["scim"],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new=AsyncMock(return_value=[{"docId": doc_id, "chunkId": chunk_id, "content": "User mapping docs"}]),
        ),
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_attributes(
            session_id=session_id,
            object_class="UserPhoneNumbers",
            db=MagicMock(),
            api_type=None,
        )

    assert response.jobId == job_id
    mock_schedule.assert_awaited_once()
    schedule_kwargs = mock_schedule.call_args.kwargs
    assert schedule_kwargs["worker_args"][1] == "userphonenumbers"
    assert schedule_kwargs["worker_args"][3] == job_input_reference("relevantDocumentations")
    mock_relevance_repo.get_relevant_chunks_grouped_by_entity.assert_awaited_once_with(
        session_id=session_id,
        result_key="objectClassesOutput",
    )
    mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_class_attributes_status_found():
    """Test getting class attributes status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()

    mock_repo.get_session_data = AsyncMock(
        side_effect=[
            str(job_id),
            {
                "attributes": {
                    "id": AttributeInfoScim(
                        type="string",
                        description="Unique identifier",
                        mandatory=True,
                    ).model_dump(),
                },
                "scimContext": {"resource": {"endpoint": "/Users"}},
            },
        ]
    )

    with (
        patch("src.modules.digester.routes.attributes.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.routes.attributes.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=MagicMock(jobId=job_id, status=JobStatus.finished, result=None),
        ) as mock_status_builder,
        patch(
            "src.modules.digester.results.hydrate_attributes_with_relevance",
            new_callable=AsyncMock,
            side_effect=lambda _db, _session_id, _result_key, payload: payload,
        ),
    ):
        session_id = uuid4()
        response = await get_class_attributes_status(
            session_id=session_id,
            object_class="user",
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert isinstance(response.result, AttributeResponse)
    assert "id" in response.result.attributes
    assert response.result.attributes["id"].type == "string"
    assert response.result.scimContext == {"resource": {"endpoint": "/Users"}}
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    assert mock_repo.get_session_data.await_args_list == [
        call(session_id, "userAttributesJobId"),
        call(session_id, "userAttributesOutput"),
    ]
    mock_status_builder.assert_awaited_once()


@pytest.mark.parametrize(
    ("protocol", "result_payload"),
    [
        pytest.param(
            "rest",
            {"attributes": {"id": {"type": "string"}}},
            id="rest",
        ),
        pytest.param(
            "scim",
            {
                "attributes": {"userName": {"type": "string", "scimAttribute": "userName"}},
                "scimContext": {"resource": {"endpoint": "/Users"}},
            },
            id="scim",
        ),
        pytest.param(
            "sql",
            {
                "attributes": {
                    "username": {
                        "type": "string",
                        "table": "app_user",
                        "column": "username",
                    }
                },
                "sqlContext": SQL_CONTEXT,
            },
            id="sql",
        ),
    ],
)
def test_attribute_status_http_serializes_sql_context_only_for_sql(protocol, result_payload):
    session_id = uuid4()
    job_id = uuid4()
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.get_session_data = AsyncMock(return_value=str(job_id))
    typed_result = AttributeResponse.model_validate(result_payload)
    status = JobStatusMultiDocResponse(
        jobId=job_id,
        status=JobStatus.finished,
        result=typed_result,
    )

    async def authenticate(request: Request):
        request.state.auth = AuthContext(AuthMode.disabled)

    app = create_api()
    app.dependency_overrides[authenticate_request] = authenticate
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with (
        patch("src.modules.digester.routes.attributes.SessionRepository", return_value=repo),
        patch(
            "src.modules.digester.routes.attributes.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=status,
        ),
        patch(
            "src.modules.digester.routes.attributes.results.refresh_attributes_status",
            new_callable=AsyncMock,
            return_value=status,
        ),
    ):
        response = TestClient(app).get(
            f"{config.app.api_base_url}/v1/digester/{session_id}/classes/{protocol}/attributes"
        )

    assert response.status_code == 200
    result = response.json()["result"]
    if protocol == "sql":
        assert result["sqlContext"] == typed_result.model_dump(mode="json")["sqlContext"]
    else:
        assert "sqlContext" not in result


@pytest.mark.asyncio
async def test_override_class_attributes_success():
    """Test manual override of class attributes."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)
    mock_repo.update_session = AsyncMock()
    mock_relevant_repo = MagicMock()
    mock_relevant_repo.replace_relevant_chunks_for_result = AsyncMock()
    chunk_id = str(uuid4())

    with (
        patch("src.modules.digester.routes.attributes.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.results.RelevantChunkRepository", return_value=mock_relevant_repo),
    ):
        session_id = uuid4()
        response = await override_class_attributes(
            session_id=session_id,
            object_class="User",
            attributes={
                "id": {
                    "type": "string",
                    "relevant_sequences": [
                        {
                            "chunkId": chunk_id,
                            "startSequence": "auth starts here",
                            "endSequence": "auth ends here",
                        }
                    ],
                }
            },
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(session_id, {"userAttributesOutput": {"id": {"type": "string"}}})
    mock_relevant_repo.replace_relevant_chunks_for_result.assert_awaited_once_with(
        session_id=session_id,
        result_key="userAttributesOutput",
        chunks=[
            {
                "result_key": "userAttributesOutput",
                "entity_key": "id",
                "doc_id": None,
                "chunk_id": chunk_id,
                "relevant_sequence": {
                    "startSequence": "auth starts here",
                    "endSequence": "auth ends here",
                },
            }
        ],
    )
    assert response["message"].startswith("Attributes for user overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "user"
