# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import orchestration
from src.modules.digester.errors import (
    EndpointExtractionNotSupportedError,
    SqlPhysicalSchemaNotFoundError,
    SqlTableIdentityConflictError,
)
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
from src.modules.digester.schemas import AttributeResponse, ObjectClassesResponse
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


def _conndev_sql_doc(
    table: str,
    database_schema: str,
    attributes: list[dict],
    *,
    object_class: str | None = None,
) -> dict:
    """A midPoint ``ri:conndev_sql`` object-class export, as uploaded by the connector-development tool."""
    object_class = object_class or table
    doc = _sql_doc(
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
                "uid": object_class,
                "name": object_class,
                "attributes": attributes,
            }
        )
    )
    doc["@metadata"]["content_type"] = "application/com.evolveum.conndev+json"
    return doc


def _conndev_sql_table_doc(
    table: str,
    database_schema: str,
    columns: list[dict],
    *,
    catalog: str = "midpoint_db",
    table_type: str = "TABLE",
) -> dict:
    """A conndev SQL-table export with its table metadata serialized in ``tableContent``."""
    table_content = {
        "catalog": catalog,
        "schema": database_schema,
        "name": table,
        "tableType": table_type,
        "remarks": None,
        "definition": "",
        "columns": columns,
    }
    doc = _sql_doc(
        json.dumps(
            {
                "definition": "",
                "catalog": catalog,
                "tableContent": json.dumps(table_content),
                "schema": database_schema,
                "tableType": table_type,
                "name": table,
                "uid": f"{catalog}.{database_schema}.{table}",
            }
        )
    )
    doc["@metadata"]["content_type"] = "application/com.evolveum.conndev+json"
    return doc


def _sql_table_column(
    name: str,
    type_name: str,
    *,
    nullable: bool = True,
    primary_key: bool = False,
    referenced_table: str | None = None,
    referenced_column: str | None = None,
    foreign_key_name: str | None = None,
) -> dict:
    return {
        "name": name,
        "typeName": type_name,
        "typeCode": 0,
        "size": 0,
        "javaType": "java.lang.String",
        "nullable": nullable,
        "primaryKey": primary_key,
        "unique": primary_key,
        "defaultValue": None,
        "remarks": None,
        "autoIncrement": False,
        "referencedTable": referenced_table,
        "referencedColumn": referenced_column,
        "foreignKeyName": foreign_key_name,
    }


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


def test_collect_sql_tables_preserves_qualified_multi_schema_ddl_identity():
    doc = _sql_doc(
        """
        CREATE TABLE schema_a.users (id UUID PRIMARY KEY);
        CREATE TABLE schema_b.users (email VARCHAR(255) NOT NULL);
        """
    )

    tables = collect_sql_tables([doc])

    assert [table["objectClass"] for table in tables] == ["schema_a.users", "schema_b.users"]
    assert [table["databaseSchema"] for table in tables] == ["schema_a", "schema_b"]
    assert [column["name"] for column in tables[0]["columns"]] == ["id"]
    assert [column["name"] for column in tables[1]["columns"]] == ["email"]


def test_collect_sql_tables_preserves_catalog_and_schema_from_raw_json():
    doc = _sql_doc(
        json.dumps(
            {
                "tables": [
                    {"catalog": "database1", "schema": "schema_a", "name": "users", "columns": []},
                    {"catalog": "database1", "schema": "schema_b", "name": "users", "columns": []},
                ]
            }
        )
    )

    tables = collect_sql_tables([doc])

    assert [table["objectClass"] for table in tables] == [
        "database1.schema_a.users",
        "database1.schema_b.users",
    ]


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


@pytest.mark.parametrize(
    "schema",
    [
        '{"tables": [{"name": "users", "columns": [{"name": "id", "type": "uuid", "generated": true}]}]}',
        "CREATE TABLE users (id UUID GENERATED ALWAYS AS IDENTITY);",
    ],
)
def test_collect_sql_tables_preserves_generated_column_metadata(schema):
    table = collect_sql_tables([_sql_doc(schema)])[0]

    assert table["columns"][0]["generated"] is True


def _ranking_passthrough() -> AsyncMock:
    """Stand in for the shared ranking step, echoing the candidates it was handed."""

    async def _rank(
        candidates,
        _job_id,
        _class_to_chunks=None,
        *,
        class_to_chunks=None,
        ranking_descriptions=None,
        intent=None,
    ):
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
    assert candidates[0].description == "Database table 'midpoint_user.m_user' with no documented columns."
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
    assert table["columns"] == []
    assert table["connectorObjectClass"] == {
        "name": "m_user",
        "attributes": [
            {"name": "nameorig", "connIdType": "string", "mandatory": True},
            {"name": "createtimestamp", "connIdType": "zoneddatetime"},
            {"name": "oid", "connIdType": "string", "creatable": False, "updatable": False},
        ],
    }
    assert table["relevantDocumentations"] == [{"docId": doc["docId"], "chunkId": doc["chunkId"]}]


def test_collect_sql_tables_preserves_logical_attribute_name_and_physical_column():
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("Username", "string", column="nameorig", required=True)],
    )

    table = collect_sql_tables([doc])[0]

    assert table["columns"] == []
    assert table["connectorObjectClass"]["attributes"] == [
        {"name": "Username", "column": "nameorig", "connIdType": "string", "mandatory": True}
    ]


def test_collect_sql_tables_reads_conndev_sql_table_constraints():
    doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "oid",
                "UUID",
                nullable=False,
                primary_key=True,
                referenced_table="m_object_oid",
                referenced_column="oid",
                foreign_key_name="m_user_oid_fkey",
            ),
            _sql_table_column("nameorig", "VARCHAR", nullable=False),
        ],
    )

    table = collect_sql_tables([doc])[0]

    assert table["table"] == "m_user"
    assert table["databaseCatalog"] == "midpoint_db"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["source"] == "conndev_sql_table"
    assert table["primaryKey"] == ["oid"]
    assert table["foreignKeys"] == [
        {
            "constraintName": "m_user_oid_fkey",
            "columns": ["oid"],
            "referencedTable": "m_object_oid",
            "referencedColumns": ["oid"],
        }
    ]
    assert table["columns"][0] == {
        "name": "oid",
        "type": "UUID",
        "nullable": False,
        "primaryKey": True,
        "unique": True,
        "generated": False,
        "foreignKey": {
            "constraintName": "m_user_oid_fkey",
            "referencedTable": "m_object_oid",
            "referencedColumn": "oid",
        },
    }
    assert table["relevantDocumentations"] == [{"docId": doc["docId"], "chunkId": doc["chunkId"]}]


def test_collect_sql_tables_accepts_outer_identity_when_table_content_omits_it():
    doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [_sql_table_column("oid", "UUID", nullable=False, primary_key=True)],
    )
    content = json.loads(doc["content"])
    table_content = json.loads(content["tableContent"])
    for field in ("catalog", "schema", "name", "tableType"):
        table_content.pop(field)
    content["tableContent"] = json.dumps(table_content)
    doc["content"] = json.dumps(content)

    table = collect_sql_tables([doc])[0]

    assert table["table"] == "m_user"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["primaryKey"] == ["oid"]


def test_collect_sql_tables_leaves_primary_key_unknown_for_empty_columns():
    table = collect_sql_tables([_conndev_sql_table_doc("m_user", "midpoint_user", [])])[0]

    assert "primaryKey" not in table


def test_collect_sql_tables_records_authoritative_absence_of_primary_key():
    table = collect_sql_tables(
        [_conndev_sql_table_doc("m_user", "midpoint_user", [_sql_table_column("name", "VARCHAR")])]
    )[0]

    assert table["primaryKey"] == []


def test_collect_sql_tables_groups_composite_conndev_foreign_key():
    table = collect_sql_tables(
        [
            _conndev_sql_table_doc(
                "m_assignment",
                "midpoint_user",
                [
                    _sql_table_column(
                        "owner_oid",
                        "UUID",
                        primary_key=True,
                        referenced_table="m_object",
                        referenced_column="oid",
                        foreign_key_name="m_assignment_owner_fkey",
                    ),
                    _sql_table_column(
                        "owner_type",
                        "INTEGER",
                        primary_key=True,
                        referenced_table="m_object",
                        referenced_column="objecttype",
                        foreign_key_name="m_assignment_owner_fkey",
                    ),
                ],
            )
        ]
    )[0]

    assert table["primaryKey"] == ["owner_oid", "owner_type"]
    assert table["foreignKeys"] == [
        {
            "constraintName": "m_assignment_owner_fkey",
            "columns": ["owner_oid", "owner_type"],
            "referencedTable": "m_object",
            "referencedColumns": ["oid", "objecttype"],
        }
    ]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_conndev_object_class_and_sql_table(reverse_order):
    object_class_doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("UID", "string", column="oid", creatable=True, updateable=False)],
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "oid",
                "UUID",
                nullable=False,
                primary_key=True,
                referenced_table="m_object_oid",
                referenced_column="oid",
                foreign_key_name="m_user_oid_fkey",
            )
        ],
    )
    docs = [object_class_doc, sql_table_doc]
    if reverse_order:
        docs.reverse()

    table = collect_sql_tables(docs)[0]

    assert table["objectClass"] == "m_user"
    assert table["source"] == "conndev_sql_table"
    assert table["primaryKey"] == ["oid"]
    assert [column["name"] for column in table["columns"]] == ["oid"]
    assert table["columns"][0]["type"] == "UUID"
    assert table["connectorObjectClass"] == {
        "name": "m_user",
        "attributes": [
            {
                "name": "UID",
                "column": "oid",
                "connIdType": "string",
                "creatable": True,
                "updatable": False,
            }
        ],
    }
    assert table["relevantDocumentations"] == [
        {"docId": sql_table_doc["docId"], "chunkId": sql_table_doc["chunkId"]},
        {"docId": object_class_doc["docId"], "chunkId": object_class_doc["chunkId"]},
    ]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_quoted_qualified_conndev_table_names(reverse_order):
    object_class_doc = _conndev_sql_doc(
        "m_user",
        "public",
        [_conndev_attribute("oid", "string")],
    )
    object_class_content = json.loads(object_class_doc["content"])
    object_class_content["sql"]["object"]["attributes"]["table"] = '"public.m_user"'
    object_class_doc["content"] = json.dumps(object_class_content)

    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "public",
        [_sql_table_column("oid", "UUID", nullable=False, primary_key=True)],
    )
    sql_table_content = json.loads(sql_table_doc["content"])
    inner_table = json.loads(sql_table_content["tableContent"])
    inner_table["name"] = '"public.m_user"'
    sql_table_content["tableContent"] = json.dumps(inner_table)
    sql_table_doc["content"] = json.dumps(sql_table_content)

    docs = [object_class_doc, sql_table_doc]
    if reverse_order:
        docs.reverse()

    tables = collect_sql_tables(docs)

    assert len(tables) == 1
    assert tables[0]["table"] == "m_user"
    assert tables[0]["objectClass"] == "m_user"
    assert tables[0]["primaryKey"] == ["oid"]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_rejects_mismatched_cross_source_schema(reverse_order):
    object_class_doc = _conndev_sql_doc(
        "users",
        "schema_a",
        [_conndev_attribute("Username", "string", column="username")],
        object_class="SchemaAUser",
    )
    sql_table_doc = _conndev_sql_table_doc(
        "users",
        "schema_b",
        [_sql_table_column("email", "VARCHAR")],
        catalog="database1",
    )
    docs = [object_class_doc, sql_table_doc]
    if reverse_order:
        docs.reverse()

    with pytest.raises(SqlTableIdentityConflictError) as exc_info:
        collect_sql_tables(docs)

    assert "schema_a.users" in str(exc_info.value)
    assert "database1.schema_b.users" in str(exc_info.value)


def test_collect_sql_tables_rejects_ambiguous_catalog_for_unqualified_logical_source():
    docs = [
        _conndev_sql_doc(
            "users",
            "identity",
            [_conndev_attribute("Username", "string", column="username")],
            object_class="User",
        ),
        _conndev_sql_table_doc(
            "users",
            "identity",
            [_sql_table_column("username", "VARCHAR")],
            catalog="database1",
        ),
        _conndev_sql_table_doc(
            "users",
            "identity",
            [_sql_table_column("username", "VARCHAR")],
            catalog="database2",
        ),
    ]

    with pytest.raises(SqlTableIdentityConflictError) as exc_info:
        collect_sql_tables(docs)

    assert "database1.identity.users" in str(exc_info.value)
    assert "database2.identity.users" in str(exc_info.value)


def test_collect_sql_tables_qualifies_one_public_class_bound_to_two_schemas():
    docs = [
        _conndev_sql_doc("users", "schema_a", [], object_class="User"),
        _conndev_sql_doc("users", "schema_b", [], object_class="User"),
    ]

    tables = collect_sql_tables(docs)

    assert [table["objectClass"] for table in tables] == ["schema_a.users", "schema_b.users"]


def test_collect_sql_tables_qualifies_colliding_sql_table_only_classes():
    docs = [
        _conndev_sql_table_doc("users", "schema_a", [], catalog="database1"),
        _conndev_sql_table_doc("users", "schema_b", [], catalog="database1"),
    ]

    tables = collect_sql_tables(docs)

    assert [table["objectClass"] for table in tables] == [
        "database1.schema_a.users",
        "database1.schema_b.users",
    ]


def test_collect_sql_tables_preserves_distinct_exported_class_names_across_schemas():
    docs = [
        _conndev_sql_doc("users", "schema_a", [], object_class="SchemaAUser"),
        _conndev_sql_doc("users", "schema_b", [], object_class="SchemaBUser"),
    ]

    tables = collect_sql_tables(docs)

    assert [table["objectClass"] for table in tables] == ["SchemaAUser", "SchemaBUser"]


@pytest.mark.asyncio
async def test_multi_schema_qualified_class_round_trips_to_attributes(mock_digester_update_job_progress):
    docs = [
        _conndev_sql_doc(
            "users",
            "schema_a",
            [_conndev_attribute("SchemaAId", "string", column="id")],
            object_class="User",
        ),
        _conndev_sql_table_doc(
            "users",
            "schema_a",
            [_sql_table_column("id", "UUID", nullable=False, primary_key=True)],
            catalog="database1",
        ),
        _conndev_sql_doc(
            "users",
            "schema_b",
            [_conndev_attribute("SchemaBEmail", "string", column="email")],
            object_class="User",
        ),
        _conndev_sql_table_doc(
            "users",
            "schema_b",
            [_sql_table_column("email", "VARCHAR", nullable=False)],
            catalog="database1",
        ),
    ]

    tables = collect_sql_tables(docs)
    ranking = _ranking_passthrough()
    with (
        patch("src.modules.digester.extractors.sql.object_class.deduplicate_and_sort_sql_object_classes", ranking),
        patch(
            "src.modules.digester.extractors.object_class.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
    ):
        object_classes_result = await extract_object_classes(docs, uuid4(), uuid4())

    schema_a_result = await extract_sql_attributes(docs, "database1.schema_a.users", uuid4())
    schema_b_result = await extract_sql_attributes(docs, "database1.schema_b.users", uuid4())

    assert [table["objectClass"] for table in tables] == [
        "database1.schema_a.users",
        "database1.schema_b.users",
    ]
    assert [item["name"] for item in object_classes_result["result"]["objectClasses"]] == [
        "database1.schema_a.users",
        "database1.schema_b.users",
    ]
    schema_a_attributes = schema_a_result["result"]["attributes"]
    schema_b_attributes = schema_b_result["result"]["attributes"]
    assert set(schema_a_attributes) == {"id"}
    assert schema_a_attributes["id"]["databaseCatalog"] == "database1"
    assert schema_a_attributes["id"]["databaseSchema"] == "schema_a"
    assert set(schema_b_attributes) == {"email"}
    assert schema_b_attributes["email"]["databaseCatalog"] == "database1"
    assert schema_b_attributes["email"]["databaseSchema"] == "schema_b"


def test_collect_sql_tables_skips_mismatched_conndev_sql_table(caplog):
    doc = _conndev_sql_table_doc("m_user", "midpoint_user", [])
    content = json.loads(doc["content"])
    content["name"] = "m_role"
    doc["content"] = json.dumps(content)

    with caplog.at_level("WARNING", logger="src.modules.digester.extractors.sql.conndev_schema"):
        tables = collect_sql_tables([doc])

    assert tables == []
    assert "mismatched name metadata" in caplog.text


def test_collect_sql_tables_skips_invalid_conndev_table_content(caplog):
    doc = _conndev_sql_table_doc("m_user", "midpoint_user", [])
    content = json.loads(doc["content"])
    content["tableContent"] = "{invalid"
    doc["content"] = json.dumps(content)

    with caplog.at_level("WARNING", logger="src.modules.digester.extractors.sql.conndev_schema"):
        tables = collect_sql_tables([doc])

    assert tables == []
    assert "invalid tableContent JSON" in caplog.text


def test_collect_sql_tables_ignores_partial_conndev_foreign_key(caplog):
    doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [_sql_table_column("tenant_oid", "UUID", referenced_table="m_tenant")],
    )

    with caplog.at_level("WARNING", logger="src.modules.digester.extractors.sql.conndev_schema"):
        table = collect_sql_tables([doc])[0]

    assert "foreignKey" not in table["columns"][0]
    assert table["foreignKeys"] == []
    assert "incomplete foreign key metadata" in caplog.text


def test_collect_sql_tables_preserves_foreign_key_target_without_constraint_name():
    doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "tenant_oid",
                "UUID",
                referenced_table="m_tenant",
                referenced_column="oid",
            )
        ],
    )

    table = collect_sql_tables([doc])[0]

    assert table["columns"][0]["foreignKey"] == {
        "referencedTable": "m_tenant",
        "referencedColumn": "oid",
    }
    assert table["foreignKeys"] == []


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
    assert "source" not in table
    assert table["primaryKey"] == ["nameorig"]
    assert table["columns"] == [{"name": "nameorig", "type": "VARCHAR(255)", "nullable": False, "primaryKey": True}]
    assert table["connectorObjectClass"]["attributes"] == [
        {
            "name": "Username",
            "column": "nameorig",
            "connIdType": "string",
            "mandatory": True,
            "creatable": False,
            "updatable": False,
        }
    ]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_raw_ddl_and_conndev_sql_table(reverse_order):
    """The conndev SQL-table export is the physical authority; a raw DDL for the same table
    only supplies fields it alone carries, regardless of upload order."""
    ddl_doc = _sql_doc(
        """
        CREATE TABLE m_user (
          nameorig VARCHAR(255) NOT NULL,
          legacy_flag BOOLEAN
        );
        """
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "nameorig",
                "VARCHAR",
                nullable=False,
                primary_key=True,
                referenced_table="m_object_oid",
                referenced_column="oid",
                foreign_key_name="m_user_nameorig_fkey",
            )
        ],
    )
    docs = [ddl_doc, sql_table_doc]
    if reverse_order:
        docs.reverse()

    tables = collect_sql_tables(docs)

    assert len(tables) == 1
    table = tables[0]
    assert table["table"] == "m_user"
    assert table["source"] == "conndev_sql_table"
    assert "objectClass" not in table
    assert table["databaseCatalog"] == "midpoint_db"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["primaryKey"] == ["nameorig"]
    assert table["foreignKeys"] == [
        {
            "constraintName": "m_user_nameorig_fkey",
            "columns": ["nameorig"],
            "referencedTable": "m_object_oid",
            "referencedColumns": ["oid"],
        }
    ]
    assert table["columns"] == [
        {
            "name": "nameorig",
            "type": "VARCHAR",
            "nullable": False,
            "primaryKey": True,
            "unique": True,
            "generated": False,
            "foreignKey": {
                "constraintName": "m_user_nameorig_fkey",
                "referencedTable": "m_object_oid",
                "referencedColumn": "oid",
            },
        },
        {"name": "legacy_flag", "type": "BOOLEAN", "nullable": True, "primaryKey": False},
    ]
    assert table["relevantDocumentations"] == [
        {"docId": docs[0]["docId"], "chunkId": docs[0]["chunkId"]},
        {"docId": docs[1]["docId"], "chunkId": docs[1]["chunkId"]},
    ]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_raw_json_schema_and_conndev_sql_table(reverse_order):
    """Same precedence when the raw physical source is a JSON table list, and a field only the
    raw schema carries (``generated``) still fills a gap on the authoritative column."""
    json_doc = _sql_doc(
        json.dumps(
            {
                "tables": [
                    {
                        "name": "m_user",
                        "columns": [
                            {"name": "nameorig", "type": "text", "generated": True},
                            {"name": "extra_col", "type": "int"},
                        ],
                    }
                ]
            }
        )
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [_sql_table_column("nameorig", "VARCHAR", nullable=False, primary_key=True)],
    )
    docs = [json_doc, sql_table_doc]
    if reverse_order:
        docs.reverse()

    tables = collect_sql_tables(docs)

    assert len(tables) == 1
    table = tables[0]
    assert table["source"] == "conndev_sql_table"
    assert table["primaryKey"] == ["nameorig"]
    assert table["columns"] == [
        {
            "name": "nameorig",
            "type": "VARCHAR",
            "nullable": False,
            "primaryKey": True,
            "generated": False,
            "unique": True,
        },
        {"name": "extra_col", "type": "int"},
    ]


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_all_three_sql_sources_for_one_table(reverse_order):
    """Object-class export (names / ConnId flags) + SQL-table export (physical fields) + raw
    schema (gap-fill column) collapse into one coherent record."""
    object_class_doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("UID", "string", column="oid", creatable=True, updateable=False)],
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "oid",
                "UUID",
                nullable=False,
                primary_key=True,
                referenced_table="m_object_oid",
                referenced_column="oid",
                foreign_key_name="m_user_oid_fkey",
            )
        ],
    )
    ddl_doc = _sql_doc("CREATE TABLE m_user (oid UUID, audit_ts TIMESTAMP);")
    docs = [object_class_doc, sql_table_doc, ddl_doc]
    if reverse_order:
        docs.reverse()

    tables = collect_sql_tables(docs)

    assert len(tables) == 1
    table = tables[0]
    assert table["objectClass"] == "m_user"
    assert table["source"] == "conndev_sql_table"
    assert table["primaryKey"] == ["oid"]
    assert [column["name"] for column in table["columns"]] == ["oid", "audit_ts"]
    assert table["connectorObjectClass"]["attributes"][0]["name"] == "UID"
    assert {(ref["docId"], ref["chunkId"]) for ref in table["relevantDocumentations"]} == {
        (doc["docId"], doc["chunkId"]) for doc in docs
    }


@pytest.mark.parametrize("reverse_order", [False, True])
def test_collect_sql_tables_merges_sql_table_and_raw_with_compatible_unequal_identity(reverse_order):
    """A bare ``CREATE TABLE`` (no schema/catalog) still merges into the qualified SQL-table
    export instead of surviving as a second record."""
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [_sql_table_column("oid", "UUID", nullable=False, primary_key=True)],
        catalog="midpoint_db",
    )
    ddl_doc = _sql_doc("CREATE TABLE m_user (oid UUID, note TEXT);")
    docs = [sql_table_doc, ddl_doc]
    if reverse_order:
        docs.reverse()

    tables = collect_sql_tables(docs)

    assert len(tables) == 1
    table = tables[0]
    assert table["databaseCatalog"] == "midpoint_db"
    assert table["databaseSchema"] == "midpoint_user"
    assert table["primaryKey"] == ["oid"]
    assert [column["name"] for column in table["columns"]] == ["oid", "note"]


def test_collect_sql_tables_conflicts_on_ambiguous_raw_to_sql_table_match():
    """A bare raw table compatible with two different SQL-table exports is a real ambiguity."""
    docs = [
        _conndev_sql_table_doc("users", "schema_a", [_sql_table_column("id", "UUID")], catalog="database1"),
        _conndev_sql_table_doc("users", "schema_b", [_sql_table_column("id", "UUID")], catalog="database1"),
        _sql_doc("CREATE TABLE users (id UUID);"),
    ]

    with pytest.raises(SqlTableIdentityConflictError):
        collect_sql_tables(docs)


def test_collect_sql_tables_does_not_invent_tables_from_scalar_json_fields():
    """``uid``/``name`` are scalar fields, not column-less tables."""
    doc = _sql_doc(json.dumps({"uid": "m_user", "name": "m_user", "displayName": "User"}))

    assert collect_sql_tables([doc]) == []


def test_collect_sql_tables_does_not_treat_ordinary_json_as_conndev():
    doc = _sql_doc(
        json.dumps(
            {
                "uid": "schema-document",
                "name": "not-a-table",
                "sql": {},
                "tables": [{"name": "users", "columns": [{"name": "id", "type": "uuid"}]}],
            }
        )
    )
    doc["@metadata"]["content_type"] = "application/json"

    tables = collect_sql_tables([doc])

    assert len(tables) == 1
    assert tables[0]["table"] == "users"
    assert tables[0]["columns"] == [{"name": "id", "type": "uuid"}]
    assert "source" not in tables[0]


def test_collect_sql_tables_skips_conndev_sql_document_without_table_binding(caplog):
    doc = _conndev_sql_doc("m_user", "midpoint_user", [_conndev_attribute("oid", "string")])
    content = json.loads(doc["content"])
    del content["sql"]["object"]["attributes"]["table"]
    doc["content"] = json.dumps(content)

    with caplog.at_level("WARNING", logger="src.modules.digester.extractors.conndev"):
        tables = collect_sql_tables([doc])

    assert tables == []
    assert "no physical table binding" in caplog.text


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
async def test_extract_sql_attributes_rejects_projection_without_physical_schema(mock_digester_update_job_progress):
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

    with pytest.raises(SqlPhysicalSchemaNotFoundError) as exc_info:
        await extract_sql_attributes([doc], "m_user", uuid4())

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "sql_physical_schema_not_found"


@pytest.mark.asyncio
async def test_projection_binding_is_not_used_as_a_physical_schema(mock_digester_update_job_progress):
    doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("Username", "string", column="nameorig")],
    )

    with pytest.raises(SqlPhysicalSchemaNotFoundError):
        await extract_sql_attributes([doc], "m_user", uuid4())


@pytest.mark.asyncio
async def test_extract_sql_attributes_enriches_conndev_primary_and_foreign_key(
    mock_digester_update_job_progress,
):
    object_class_doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("oid", "string", creatable=True, updateable=False)],
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "oid",
                "UUID",
                nullable=False,
                primary_key=True,
                referenced_table="m_object_oid",
                referenced_column="oid",
                foreign_key_name="m_user_oid_fkey",
            )
        ],
    )

    result = await extract_sql_attributes([object_class_doc, sql_table_doc], "m_user", uuid4())

    oid = result["result"]["attributes"]["oid"]
    assert oid["primaryKey"] is True
    assert oid["foreignKey"] == {
        "constraintName": "m_user_oid_fkey",
        "referencedTable": "m_object_oid",
        "referencedColumn": "oid",
    }
    assert oid["updatable"] is False
    assert oid["mandatory"] is True
    assert result["relevantDocumentations"] == [
        {"doc_id": sql_table_doc["docId"], "chunk_id": sql_table_doc["chunkId"]},
        {"doc_id": object_class_doc["docId"], "chunk_id": object_class_doc["chunkId"]},
    ]


@pytest.mark.asyncio
async def test_app_user_keeps_physical_columns_and_projection_separate(mock_digester_update_job_progress):
    projection_doc = _conndev_sql_doc(
        "app_user",
        "public",
        [
            _conndev_attribute("__NAME__", "string", required=True, creatable=False, updateable=False),
            _conndev_attribute("login", "string", column="username"),
            _conndev_attribute("id", "string", column="id", updateable=False),
            _conndev_attribute("birthDate", "zoneddatetime", column="birthdate"),
        ],
    )
    physical_doc = _conndev_sql_table_doc(
        "app_user",
        "public",
        [
            _sql_table_column("id", "SERIAL", nullable=False, primary_key=True),
            _sql_table_column("username", "VARCHAR", nullable=False, primary_key=False),
            _sql_table_column("email", "VARCHAR", nullable=True, primary_key=False),
            _sql_table_column("created_at", "TIMESTAMP", nullable=False, primary_key=False),
            _sql_table_column("birthdate", "DATE", nullable=True, primary_key=False),
        ],
        catalog="connector_project_db",
    )

    forward = await extract_sql_attributes([projection_doc, physical_doc], "app_user", uuid4())
    reverse = await extract_sql_attributes([physical_doc, projection_doc], "app_user", uuid4())

    assert forward == reverse
    attributes = forward["result"]["attributes"]
    assert list(attributes) == ["id", "username", "email", "created_at", "birthdate"]
    assert "__NAME__" not in attributes
    assert attributes["id"]["type"] == "integer"
    assert attributes["id"]["databaseType"] == "SERIAL"
    assert attributes["birthdate"]["type"] == "string"
    assert attributes["birthdate"]["format"] == "date"
    assert attributes["birthdate"]["databaseType"] == "DATE"
    assert forward["result"]["sqlContext"] == {
        "physicalTable": {
            "databaseCatalog": "connector_project_db",
            "databaseSchema": "public",
            "table": "app_user",
            "tableType": "TABLE",
        },
        "connectorObjectClass": {
            "name": "app_user",
            "attributes": [
                {
                    "name": "__NAME__",
                    "connIdType": "string",
                    "column": None,
                    "mandatory": True,
                    "creatable": False,
                    "updatable": False,
                },
                {"name": "login", "connIdType": "string", "column": "username"},
                {"name": "id", "connIdType": "string", "column": "id", "updatable": False},
                {"name": "birthDate", "connIdType": "zoneddatetime", "column": "birthdate"},
            ],
        },
    }
    assert {(ref["doc_id"], ref["chunk_id"]) for ref in forward["relevantDocumentations"]} == {
        (projection_doc["docId"], projection_doc["chunkId"]),
        (physical_doc["docId"], physical_doc["chunkId"]),
    }
    AttributeResponse.model_validate(forward["result"])


@pytest.mark.asyncio
async def test_extract_sql_attributes_preserves_foreign_key_target_without_constraint_name(
    mock_digester_update_job_progress,
):
    object_class_doc = _conndev_sql_doc(
        "m_user",
        "midpoint_user",
        [_conndev_attribute("tenant_oid", "string")],
    )
    sql_table_doc = _conndev_sql_table_doc(
        "m_user",
        "midpoint_user",
        [
            _sql_table_column(
                "tenant_oid",
                "UUID",
                referenced_table="m_tenant",
                referenced_column="oid",
            )
        ],
    )

    result = await extract_sql_attributes([object_class_doc, sql_table_doc], "m_user", uuid4())

    assert result["result"]["attributes"]["tenant_oid"]["foreignKey"] == {
        "referencedTable": "m_tenant",
        "referencedColumn": "oid",
    }


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
async def test_extract_sql_attributes_preserves_required_and_falls_back_to_nullable(
    mock_digester_update_job_progress,
):
    doc = _sql_doc(
        """
        {"tables": [{"name": "users", "columns": [
          {"name": "required_true", "type": "varchar", "required": true},
          {"name": "required_false", "type": "varchar", "required": false},
          {"name": "not_nullable", "type": "varchar", "nullable": false},
          {"name": "nullable", "type": "varchar", "nullable": true},
          {"name": "unknown", "type": "varchar"},
          {"name": "required_true_nullable", "type": "varchar", "required": true, "nullable": true},
          {"name": "required_false_not_nullable", "type": "varchar", "required": false, "nullable": false}
        ]}]}
        """
    )

    result = await extract_sql_attributes([doc], "users", uuid4())
    attributes = result["result"]["attributes"]

    assert attributes["required_true"]["mandatory"] is True
    assert attributes["required_false"]["mandatory"] is False
    assert attributes["not_nullable"]["mandatory"] is True
    assert attributes["nullable"]["mandatory"] is False
    assert attributes["unknown"]["mandatory"] is None
    assert attributes["required_true_nullable"]["mandatory"] is True
    assert attributes["required_false_not_nullable"]["mandatory"] is False


@pytest.mark.asyncio
async def test_extract_sql_attributes_ignores_untyped_raw_foreign_key(mock_digester_update_job_progress):
    doc = _sql_doc(
        """
        {"tables": [{"name": "users", "columns": [
          {"name": "tenant_id", "type": "uuid", "foreignKey": true}
        ]}]}
        """
    )

    result = await extract_sql_attributes([doc], "User", uuid4())

    assert result["result"]["attributes"]["tenant_id"]["foreignKey"] is None
    AttributeResponse.model_validate(result["result"])


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
async def test_extract_sql_attributes_marks_generated_columns_non_creatable(
    mock_digester_update_job_progress,
):
    doc = _sql_doc("CREATE TABLE users (id UUID GENERATED ALWAYS AS IDENTITY);")

    result = await extract_sql_attributes([doc], "User", uuid4())

    assert result["result"]["attributes"]["id"]["creatable"] is False
    assert result["result"]["attributes"]["id"]["generated"] is True


@pytest.mark.asyncio
async def test_extract_sql_attributes_preserves_unique_default_and_view_facts(mock_digester_update_job_progress):
    raw_view = _sql_doc(
        json.dumps(
            {
                "tables": [
                    {
                        "name": "active_users",
                        "tableType": "VIEW",
                        "columns": [
                            {
                                "name": "username",
                                "type": "VARCHAR",
                                "nullable": False,
                                "unique": True,
                                "default": "CURRENT_USER",
                            }
                        ],
                    }
                ]
            }
        )
    )

    result = await extract_sql_attributes([raw_view], "ActiveUser", uuid4())

    username = result["result"]["attributes"]["username"]
    assert username["databaseType"] == "VARCHAR"
    assert username["nullable"] is False
    assert username["unique"] is True
    assert username["defaultValue"] == "CURRENT_USER"
    assert username["creatable"] is False
    assert username["updatable"] is False
    assert result["result"]["sqlContext"] == {"physicalTable": {"table": "active_users", "tableType": "VIEW"}}


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
