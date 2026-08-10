# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import orchestration
from src.modules.digester.errors import EndpointExtractionNotSupportedError
from src.modules.digester.extractors.conndev import detect_object_class_binding
from src.modules.digester.extractors.endpoints import extract_endpoints
from src.modules.digester.extractors.object_class import extract_object_classes
from src.modules.digester.extractors.sql.attributes import extract_sql_attributes
from src.modules.digester.extractors.sql.object_class import _describe_table
from src.modules.digester.extractors.sql.schema import (
    collect_sql_tables,
    object_class_name_for_table,
    object_class_name_from_table,
    tables_for_object_class,
)
from src.modules.digester.schemas import ObjectClassesResponse
from src.shared.enums import ApiType


def _sql_doc(content: str) -> dict:
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    return {
        "docId": doc_id,
        "chunkId": chunk_id,
        "content": content,
        "summary": "Database schema",
        "@metadata": {"tags": ["sql", "schema"]},
    }


def _conndev_attribute(name: str, connid_type: str, *, column: str | None = None, **flags) -> dict:
    attributes = {"name": name, "connId": {"type": connid_type, **flags}}
    if column is not None:
        attributes["sql"] = {"path": column}
    return {
        "type": "c:ShadowType",
        "object": {
            "exists": True,
            "objectClass": "ri:conndev_Attribute",
            "attributes": attributes,
        },
    }


def _conndev_sql_doc(table: str, database_schema: str, attributes: list[dict]) -> dict:
    """A midPoint ``ri:conndev_sql`` object-class export, as uploaded by the connector-development tool."""
    return _sql_doc(
        json.dumps(
            {
                "sql": {
                    "type": "c:ShadowType",
                    "object": {
                        "exists": True,
                        "objectClass": "ri:conndev_sql",
                        "attributes": {"table": table, "schema": database_schema},
                    },
                },
                "uid": table,
                "name": table,
                "attributes": attributes,
            }
        )
    )


@pytest.fixture(autouse=True)
def mock_sql_update_job_progress():
    with (
        patch("src.modules.digester.extractors.sql.attributes.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.sql.object_class.update_job_progress", new_callable=AsyncMock),
    ):
        yield


def test_collect_sql_tables_from_json_schema():
    doc = _sql_doc(
        """
        {
          "tables": [
            {
              "name": "app_users",
              "columns": [
                {"name": "id", "type": "uuid", "primaryKey": true},
                {"name": "email", "type": "varchar", "nullable": false}
              ]
            }
          ]
        }
        """
    )

    tables = collect_sql_tables([doc])

    assert tables == [
        {
            "table": "app_users",
            "columns": [
                {"name": "id", "type": "uuid", "primaryKey": True},
                {"name": "email", "type": "varchar", "nullable": False},
            ],
            "relevantDocumentations": [{"docId": doc["docId"], "chunkId": doc["chunkId"]}],
        }
    ]


def test_collect_sql_tables_from_create_table_ddl():
    doc = _sql_doc(
        """
        CREATE TABLE users (
          id UUID PRIMARY KEY,
          username VARCHAR(255) NOT NULL,
          active BOOLEAN
        );
        """
    )

    tables = collect_sql_tables([doc])

    assert tables[0]["table"] == "users"
    assert tables[0]["columns"][0] == {"name": "id", "type": "UUID", "nullable": True, "primaryKey": True}
    assert tables[0]["columns"][1] == {
        "name": "username",
        "type": "VARCHAR(255)",
        "nullable": False,
        "primaryKey": False,
    }


def test_collect_sql_tables_marks_table_level_primary_key_columns():
    doc = _sql_doc(
        """
        CREATE TABLE users (
          id UUID NOT NULL,
          username VARCHAR(255) NOT NULL,
          CONSTRAINT users_pkey PRIMARY KEY (id)
        );
        """
    )

    tables = collect_sql_tables([doc])

    assert tables[0]["primaryKey"] == ["id"]
    assert tables[0]["columns"][0] == {"name": "id", "type": "UUID", "nullable": False, "primaryKey": True}
    assert tables[0]["columns"][1] == {
        "name": "username",
        "type": "VARCHAR(255)",
        "nullable": False,
        "primaryKey": False,
    }


def test_collect_sql_tables_marks_composite_table_level_primary_key_columns():
    doc = _sql_doc(
        """
        CREATE TABLE user_roles (
          user_id UUID NOT NULL,
          role_id UUID NOT NULL,
          assigned_at TIMESTAMP,
          PRIMARY KEY (user_id, role_id)
        );
        """
    )

    tables = collect_sql_tables([doc])

    assert tables[0]["primaryKey"] == ["user_id", "role_id"]
    assert [column["primaryKey"] for column in tables[0]["columns"]] == [True, True, False]


def test_collect_sql_tables_projects_raw_json_table_primary_key_to_columns():
    doc = _sql_doc(
        """
        {"tables": [{
          "name": "users",
          "primaryKey": ["id"],
          "columns": [
            {"name": "id", "type": "uuid"},
            {"name": "email", "type": "varchar"}
          ]
        }]}
        """
    )

    tables = collect_sql_tables([doc])

    assert tables[0]["primaryKey"] == ["id"]
    assert [column["primaryKey"] for column in tables[0]["columns"]] == [True, False]


def _ranking_passthrough() -> AsyncMock:
    """Stand in for the shared ranking step, echoing the candidates it was handed."""

    async def _rank(candidates, _job_id, _class_to_chunks=None, *, class_to_chunks=None, ranking_descriptions=None):
        return ObjectClassesResponse(
            objectClasses=[
                {
                    "name": candidate.name,
                    "description": candidate.description,
                    "confidence": "medium",
                    "relevant": "true",
                }
                for candidate in candidates
            ]
        )

    return AsyncMock(side_effect=_rank)


def test_sql_ranking_description_is_shorter_than_final_description():
    table = {
        "table": "m_user",
        "databaseSchema": "midpoint_user",
        "columns": [{"name": f"column_{index}"} for index in range(1, 7)],
    }

    assert _describe_table(table) == (
        "Database table 'midpoint_user.m_user' with 6 columns: "
        "column_1, column_2, column_3, column_4, column_5, column_6."
    )
    assert _describe_table(table, column_sample=4) == (
        "Database table 'midpoint_user.m_user' with 6 columns: column_1, column_2, column_3, column_4, ... (+2 more)."
    )


@pytest.mark.asyncio
async def test_extract_sql_object_classes_from_raw_schema_ranks_every_table(mock_digester_update_job_progress):
    doc = _sql_doc(
        """
        {"tables": [
          {"name": "users", "columns": [
            {"name": "id"}, {"name": "username"}, {"name": "email"},
            {"name": "given_name"}, {"name": "family_name"}, {"name": "department"}
          ]},
          {"name": "qrtz_locks", "columns": [{"name": "lock_name"}]}
        ]}
        """
    )

    ranking = _ranking_passthrough()
    with (
        patch("src.modules.digester.extractors.sql.object_class.deduplicate_and_sort_sql_object_classes", ranking),
        patch(
            "src.modules.digester.extractors.object_class.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
    ):
        result = await extract_object_classes([doc], uuid4(), uuid4())

    # No name heuristic drops a table up front; relevance is the ranking step's decision.
    names = [obj_class["name"] for obj_class in result["result"]["objectClasses"]]
    assert names == ["User", "QrtzLock"]
    candidates, _job_id = ranking.await_args.args
    ranking_descriptions = ranking.await_args.kwargs["ranking_descriptions"]
    assert candidates[0].description == (
        "Database table 'users' with 6 columns: id, username, email, given_name, family_name, department."
    )
    assert ranking_descriptions["user"] == (
        "Database table 'users' with 6 columns: id, username, email, given_name, ... (+2 more)."
    )


@pytest.mark.asyncio
async def test_extract_sql_object_classes_from_conndev_export(mock_digester_update_job_progress):
    docs = [
        _conndev_sql_doc("m_user", "midpoint_user", [_conndev_attribute("nameorig", "string", required=True)]),
        _conndev_sql_doc("m_focus", "midpoint_user", [_conndev_attribute("oid", "string")]),
    ]

    ranking = _ranking_passthrough()
    with (
        patch("src.modules.digester.extractors.sql.object_class.deduplicate_and_sort_sql_object_classes", ranking),
        patch(
            "src.modules.digester.extractors.object_class.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
    ):
        result = await extract_object_classes(docs, uuid4(), uuid4())

    # The export states the object class name, so it is used verbatim - no PascalCase reshaping.
    names = [obj_class["name"] for obj_class in result["result"]["objectClasses"]]
    assert names == ["m_user", "m_focus"]

    candidates, _job_id = ranking.await_args.args
    class_to_chunks = ranking.await_args.kwargs["class_to_chunks"]
    assert candidates[0].description == "Database table 'midpoint_user.m_user' with 1 columns: nameorig."
    assert class_to_chunks["m_user"] == [{"doc_id": docs[0]["docId"], "chunk_id": docs[0]["chunkId"]}]


def test_collect_sql_tables_reads_conndev_export_columns():
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [
            _conndev_attribute("nameorig", "string", required=True),
            _conndev_attribute("createtimestamp", "zoneddatetime"),
            _conndev_attribute("oid", "string", creatable=False, updateable=False),
        ],
    )

    tables = collect_sql_tables([doc])

    assert len(tables) == 1
    table = tables[0]
    assert table["table"] == "m_user"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["source"] == "conndev"
    assert table["columns"] == [
        {"name": "nameorig", "connIdType": "string", "mandatory": True},
        {"name": "createtimestamp", "connIdType": "zoneddatetime"},
        {"name": "oid", "connIdType": "string", "creatable": False, "updatable": False},
    ]
    assert table["relevantDocumentations"] == [{"docId": doc["docId"], "chunkId": doc["chunkId"]}]


def test_collect_sql_tables_preserves_logical_attribute_name_and_physical_column():
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("Username", "string", column="nameorig", required=True)],
    )

    table = collect_sql_tables([doc])[0]

    assert table["columns"] == [{"name": "Username", "column": "nameorig", "connIdType": "string", "mandatory": True}]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_conndev_and_ddl_column_metadata(reverse_order):
    conndev_doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [
            _conndev_attribute(
                "Username",
                "string",
                column="nameorig",
                required=True,
                creatable=False,
                updateable=False,
            )
        ],
    )
    ddl_doc = _sql_doc(
        """
        CREATE TABLE m_user (
          nameorig VARCHAR(255) NOT NULL PRIMARY KEY
        );
        """
    )
    docs = [conndev_doc, ddl_doc]
    if reverse_order:
        docs.reverse()

    table = collect_sql_tables(docs)[0]

    assert table["objectClass"] == "m_user"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["source"] == "conndev"
    assert table["primaryKey"] == ["nameorig"]
    assert table["columns"] == [
        {
            "name": "Username",
            "column": "nameorig",
            "connIdType": "string",
            "mandatory": True,
            "creatable": False,
            "updatable": False,
            "type": "VARCHAR(255)",
            "nullable": False,
            "primaryKey": True,
        }
    ]


def test_collect_sql_tables_does_not_invent_tables_from_scalar_json_fields():
    """``uid``/``name`` are scalar fields, not column-less tables."""
    doc = _sql_doc(json.dumps({"uid": "m_user", "name": "m_user", "displayName": "User"}))

    assert collect_sql_tables([doc]) == []


def test_detect_object_class_binding_distinguishes_sql_from_scim():
    sql_doc = {"sql": {}, "uid": "m_user", "name": "m_user", "attributes": []}
    scim_doc = {"scim": {}, "uid": "User", "name": "User", "attributes": []}

    assert detect_object_class_binding(sql_doc) is ApiType.SQL
    assert detect_object_class_binding(scim_doc) is ApiType.SCIM
    assert detect_object_class_binding({"schemaContent": "{}"}) is None


@pytest.mark.parametrize(
    ("table_name", "expected"),
    [
        ("m_user", "MUser"),
        ("app_users", "AppUser"),
        ("m_focus", "MFocus"),
        ("m_status", "MStatus"),
        ("m_address", "MAddress"),
        ("companies", "Company"),
    ],
)
def test_object_class_name_from_table_keeps_false_plurals(table_name: str, expected: str):
    """Name derivation is the fallback for a raw schema, which carries no object-class name."""
    assert object_class_name_from_table(table_name) == expected


def test_object_class_name_prefers_the_exported_conndev_name():
    conndev_table = {"table": "m_user", "objectClass": "m_user"}
    raw_schema_table = {"table": "app_users"}

    assert object_class_name_for_table(conndev_table) == "m_user"
    assert object_class_name_for_table(raw_schema_table) == "AppUser"


def test_tables_for_object_class_matches_the_exported_name():
    tables = [{"table": "m_user", "objectClass": "m_user", "columns": []}]

    assert tables_for_object_class(tables, "m_user") == tables
    assert tables_for_object_class(tables, "M_User") == tables


@pytest.mark.asyncio
async def test_extract_sql_attributes_from_conndev_export(mock_digester_update_job_progress):
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [
            _conndev_attribute("nameorig", "string", required=True),
            _conndev_attribute("createtimestamp", "zoneddatetime"),
            _conndev_attribute("modifychannelid", "integer"),
            _conndev_attribute("oid", "string", creatable=False, updateable=False),
            _conndev_attribute("photo", "binary"),
        ],
    )

    result = await extract_sql_attributes([doc], "m_user", uuid4())

    attributes = result["result"]["attributes"]
    assert set(attributes) == {"nameorig", "createtimestamp", "modifychannelid", "oid", "photo"}
    assert attributes["nameorig"]["mandatory"] is True
    assert attributes["createtimestamp"]["type"] == "string"
    assert attributes["createtimestamp"]["format"] == "date-time"
    assert attributes["modifychannelid"]["type"] == "integer"
    assert attributes["photo"]["format"] == "binary"
    assert attributes["oid"]["creatable"] is False
    assert attributes["oid"]["updatable"] is False
    assert attributes["nameorig"]["description"] == "Column 'nameorig' from table 'midpoint_user.m_user'."
    assert result["relevantDocumentations"] == [{"doc_id": doc["docId"], "chunk_id": doc["chunkId"]}]


@pytest.mark.asyncio
async def test_extract_sql_attributes_maps_logical_name_to_physical_column(mock_digester_update_job_progress):
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("Username", "string", column="nameorig")],
    )

    result = await extract_sql_attributes([doc], "m_user", uuid4())

    assert set(result["result"]["attributes"]) == {"Username"}
    assert result["result"]["attributes"]["Username"]["column"] == "nameorig"
    assert result["result"]["attributes"]["Username"]["description"] == (
        "Column 'nameorig' from table 'midpoint_user.m_user'."
    )


@pytest.mark.asyncio
async def test_extract_sql_attributes_from_table_columns(mock_digester_update_job_progress):
    doc = _sql_doc(
        """
        {"tables": [{"name": "users", "columns": [
          {"name": "id", "type": "uuid", "primaryKey": true},
          {"name": "email", "type": "varchar", "nullable": false},
          {"name": "active", "type": "boolean"}
        ]}]}
        """
    )

    result = await extract_sql_attributes([doc], "User", uuid4())

    attributes = result["result"]["attributes"]
    assert attributes["id"]["type"] == "string"
    assert attributes["id"]["updatable"] is False
    assert attributes["email"]["mandatory"] is True
    assert attributes["active"]["type"] == "boolean"


@pytest.mark.asyncio
async def test_extract_sql_attributes_treats_table_level_primary_key_as_non_updatable(
    mock_digester_update_job_progress,
):
    doc = _sql_doc(
        """
        CREATE TABLE users (
          id UUID NOT NULL,
          email VARCHAR(255) NOT NULL,
          PRIMARY KEY (id)
        );
        """
    )

    result = await extract_sql_attributes([doc], "User", uuid4())

    attributes = result["result"]["attributes"]
    assert attributes["id"]["primaryKey"] is True
    assert attributes["id"]["updatable"] is False
    assert attributes["email"]["updatable"] is True


@pytest.mark.asyncio
async def test_extract_sql_attributes_projects_raw_json_composite_primary_key(
    mock_digester_update_job_progress,
):
    doc = _sql_doc(
        """
        {"tables": [{
          "name": "user_roles",
          "primaryKey": ["user_id", "role_id"],
          "columns": [
            {"name": "user_id", "type": "uuid"},
            {"name": "role_id", "type": "uuid"},
            {"name": "assigned_at", "type": "timestamp"}
          ]
        }]}
        """
    )

    result = await extract_sql_attributes([doc], "UserRole", uuid4())

    attributes = result["result"]["attributes"]
    assert attributes["user_id"]["primaryKey"] is True
    assert attributes["user_id"]["updatable"] is False
    assert attributes["role_id"]["primaryKey"] is True
    assert attributes["role_id"]["updatable"] is False
    assert attributes["assigned_at"]["primaryKey"] is False
    assert attributes["assigned_at"]["updatable"] is True


@pytest.mark.asyncio
async def test_endpoint_extraction_is_rejected_for_a_sql_session():
    """A database connector has no endpoints, so the request must fail instead of extracting nothing."""
    with patch(
        "src.modules.digester.extractors.endpoints.resolve_effective_api_type",
        new_callable=AsyncMock,
        return_value=ApiType.SQL,
    ):
        with pytest.raises(EndpointExtractionNotSupportedError) as excinfo:
            await extract_endpoints([_sql_doc("CREATE TABLE m_user (oid uuid);")], "m_user", uuid4(), [], uuid4())

    assert "not applicable" in str(excinfo.value)


@pytest.mark.asyncio
async def test_endpoint_extraction_is_rejected_before_a_job_is_created():
    """The request path rejects it too, so no job row and no documentation selection happen."""
    repo = MagicMock()
    repo.db = MagicMock()

    with (
        patch(
            "src.modules.digester.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
        patch("src.modules.digester.orchestration.DocumentationSelector") as mock_selector,
    ):
        with pytest.raises(EndpointExtractionNotSupportedError):
            await orchestration.schedule_endpoint_extraction(
                db=MagicMock(),
                repo=repo,
                session_id=uuid4(),
                object_class="m_user",
                skip_cache=False,
                api_type=None,
            )

    mock_schedule.assert_not_awaited()
    mock_selector.assert_not_called()
