# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Deterministic SCIM endpoint pregeneration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim import endpoints as scim_endpoints

_MODULE = scim_endpoints.__name__
_SCHEMA_DIR = Path(__file__).parent / "scim_schemas"


def _baseline_schemas() -> dict:
    """Load the SCIM baseline schemas that stand in for a session's conndev documents."""
    schemas: dict = {}
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        schemas[schema["name"]] = schema
    return schemas


BASELINE_SCHEMAS = _baseline_schemas()


class _NoopAsyncSession:
    """Async context manager standing in for ``async_session_maker()`` in unit tests."""

    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


async def _run_pregenerate(object_class: str, schemas: dict | None = None, object_classes_output=None) -> dict:
    """Invoke pregenerate_scim_endpoints with the DB and baseline-schema loading stubbed out."""
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value=object_classes_output)

    with (
        patch(f"{_MODULE}.update_job_progress", new_callable=AsyncMock),
        patch(f"{_MODULE}.increment_processed_documents", new_callable=AsyncMock),
        patch(f"{_MODULE}.async_session_maker", return_value=_NoopAsyncSession()),
        patch(f"{_MODULE}.SessionRepository", return_value=repo),
        patch(
            f"{_MODULE}.load_session_scim_schemas",
            new_callable=AsyncMock,
            return_value=BASELINE_SCHEMAS if schemas is None else schemas,
        ),
    ):
        return await scim_endpoints.pregenerate_scim_endpoints(
            session_id=uuid4(),
            object_class=object_class,
            job_id=uuid4(),
            relevant_chunks=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "object_class, expected_path",
    [("user", "/Users"), ("group", "/Groups")],
)
async def test_pregenerate_keeps_canonical_casing_for_standard_resources(object_class, expected_path):
    """
    object_class reaches the extractor lower-cased (normalize_object_class_name), but standard SCIM
    resources must keep their schema casing so the paths stay RFC-7644 conformant (/Users, not /users).
    """
    result = await _run_pregenerate(object_class)
    endpoints = result["result"]["endpoints"]

    assert endpoints, "expected CRUD endpoints for a standard schema-backed resource"
    assert all(ep["path"] in (expected_path, f"{expected_path}/{{id}}") for ep in endpoints)


@pytest.mark.asyncio
async def test_pregenerate_infers_path_for_schema_backed_custom_resource():
    """A conndev schema-backed custom resource (not a built-in) still gets CRUD endpoints."""
    schemas = {**BASELINE_SCHEMAS, "Account": {"id": "urn:example:conndev:schemas:Account", "name": "Account"}}
    result = await _run_pregenerate("account", schemas=schemas)

    endpoints = result["result"]["endpoints"]
    assert endpoints, "schema-backed custom resource must not produce empty endpoints"
    assert all(ep["path"] in ("/Accounts", "/Accounts/{id}") for ep in endpoints)


@pytest.mark.asyncio
async def test_pregenerate_skips_extension_schema():
    """SCIM extension schemas (e.g. EnterpriseUser) augment another resource and get no endpoints."""
    result = await _run_pregenerate("enterpriseuser")
    assert result["result"]["endpoints"] == []
