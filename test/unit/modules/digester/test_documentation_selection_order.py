# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Regression: conflicting definitions resolved by position must not share a cache identity.

The SCIM baseline lets the last conflicting definition win and SQL schema collection lets
the first one win. Two sessions holding the same conflicting documents in opposite order
therefore extract different attributes, and must not reuse each other's cached result.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.documents.selection import SelectionRole
from src.modules.digester.extractors.scim.baseline import build_scim_baseline_from_documents
from src.modules.digester.extractors.sql.schema import collect_sql_tables
from src.modules.digester.selection import DocumentationSelectionPlan, DocumentationSelector
from src.shared.enums import ApiType
from src.shared.normalize import DOCUMENTATION_SELECTION_INPUT_KEY, normalized_input_fingerprint

CONNDEV = "application/com.evolveum.conndev+json"


def _scim_user_schema_export(attribute: str) -> dict[str, Any]:
    schema = {"id": "urn:ietf:params:scim:schemas:core:2.0:User", "name": "User", "attributes": [{"name": attribute}]}
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": None,
        "summary": f"SCIM schema export with {attribute}",
        "content": json.dumps({"name": "User", "id": schema["id"], "schemaContent": json.dumps(schema)}),
        "@metadata": {"category": "spec_json", "tags": ["scim", "schema", "conndev"], "content_type": CONNDEV},
    }


def _sql_table(username_type: str) -> dict[str, Any]:
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": None,
        "summary": f"app_user with a {username_type} username",
        "content": f"CREATE TABLE app_user (id UUID PRIMARY KEY, username {username_type} NOT NULL);",
        "@metadata": {"category": "reference_other", "tags": ["sql"], "content_type": "application/sql"},
    }


def _renumbered(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "chunkId": str(uuid4()), "docId": str(uuid4())}


async def _attribute_plan(corpus: list[dict[str, Any]], object_class: str, protocol: ApiType):
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": object_class}]})
    relevant_repo = MagicMock(get_relevant_chunks_grouped_by_entity=AsyncMock(return_value={}))
    selector = DocumentationSelector(
        MagicMock(),
        load_corpus=AsyncMock(return_value=corpus),
        relevant_repo_factory=lambda _db: relevant_repo,
    )
    return await selector.build_attribute_plan(
        repo=repo, session_id=uuid4(), object_class=object_class, protocol=protocol
    )


def _fingerprint(plan: DocumentationSelectionPlan, object_class: str, protocol: ApiType) -> str:
    return normalized_input_fingerprint(
        {
            "objectClass": object_class,
            "apiType": protocol.value,
            DOCUMENTATION_SELECTION_INPUT_KEY: plan.selection.to_job_input(),
        }
    )


@pytest.mark.asyncio
async def test_conflicting_scim_schema_exports_in_opposite_order_do_not_share_a_cache_identity():
    older, newer = _scim_user_schema_export("userName"), _scim_user_schema_export("emails")

    forward = await _attribute_plan([older, newer], "User", ApiType.SCIM)
    reverse = await _attribute_plan([_renumbered(newer), _renumbered(older)], "User", ApiType.SCIM)

    def winner(plan: DocumentationSelectionPlan) -> list[str]:
        bundle = build_scim_baseline_from_documents(plan.selection.documentation_items(SelectionRole.SCIM_BASELINE))
        return [attribute["name"] for attribute in bundle.schemas["User"]["attributes"]]

    # The last export wins, so the two sessions really extract different schemas ...
    assert winner(forward) == ["emails"]
    assert winner(reverse) == ["userName"]
    # ... and therefore must never reuse each other's cached attributes.
    assert _fingerprint(forward, "user", ApiType.SCIM) != _fingerprint(reverse, "user", ApiType.SCIM)


@pytest.mark.asyncio
async def test_same_scim_exports_in_the_same_order_still_share_a_cache_identity():
    older, newer = _scim_user_schema_export("userName"), _scim_user_schema_export("emails")

    session_a = await _attribute_plan([older, newer], "User", ApiType.SCIM)
    session_b = await _attribute_plan([_renumbered(older), _renumbered(newer)], "User", ApiType.SCIM)

    assert _fingerprint(session_a, "user", ApiType.SCIM) == _fingerprint(session_b, "user", ApiType.SCIM)


@pytest.mark.asyncio
async def test_conflicting_sql_table_definitions_in_opposite_order_do_not_share_a_cache_identity():
    text_table, integer_table = _sql_table("TEXT"), _sql_table("INTEGER")

    forward = await _attribute_plan([text_table, integer_table], "app_user", ApiType.SQL)
    reverse = await _attribute_plan([_renumbered(integer_table), _renumbered(text_table)], "app_user", ApiType.SQL)

    def username_type(plan: DocumentationSelectionPlan) -> str:
        tables = collect_sql_tables(plan.selection.documentation_items(SelectionRole.SQL_SCHEMA))
        return next(column["type"] for column in tables[0]["columns"] if column["name"] == "username")

    # The first definition wins ...
    assert username_type(forward) == "TEXT"
    assert username_type(reverse) == "INTEGER"
    # ... so the opposite order is a different job.
    assert _fingerprint(forward, "app_user", ApiType.SQL) != _fingerprint(reverse, "app_user", ApiType.SQL)
