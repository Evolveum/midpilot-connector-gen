# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.extractors.sql.attributes import extract_sql_attributes
from src.modules.digester.extractors.sql.schema import collect_sql_tables
from src.modules.digester.extractors.sql.tables import extract_sql_tables
from src.modules.digester.schemas import ExtendedObjectClass, ObjectClassesExtendedResponse


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


@pytest.mark.asyncio
async def test_extract_sql_object_classes_uses_heuristics_and_single_llm_call(mock_digester_update_job_progress):
    doc = _sql_doc(
        """
        {"tables": [{"name": "users", "columns": [{"name": "id"}, {"name": "username"}, {"name": "email"}]}]}
        """
    )

    class FakeChain:
        ainvoke = AsyncMock(
            return_value=ObjectClassesExtendedResponse(
                objectClasses=[
                    ExtendedObjectClass(
                        name="User",
                        description="Application account holder.",
                        superclass=None,
                        abstract=False,
                        embedded=False,
                    )
                ]
            )
        )

    with (
        patch(
            "src.modules.digester.extractors.sql.object_class.build_structured_chain", return_value=FakeChain()
        ) as build_chain,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=["SQL"]),
    ):
        result = await service.extract_object_classes([doc], uuid4(), uuid4())

    assert result["result"]["objectClasses"][0]["name"] == "User"
    build_chain.assert_called_once()
    FakeChain.ainvoke.assert_awaited_once()


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
async def test_extract_sql_tables_returns_codegen_compatible_endpoints_key(mock_digester_update_job_progress):
    doc = _sql_doc(
        """
        {"tables": [{"name": "users", "columns": [{"name": "id", "type": "uuid"}]}]}
        """
    )

    result = await extract_sql_tables([doc], "User", uuid4())

    assert list(result["result"]) == ["endpoints"]
    assert result["result"]["endpoints"][0]["table"] == "users"
