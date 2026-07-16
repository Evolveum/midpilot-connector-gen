# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Deterministic SCIM endpoint pregeneration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim import endpoints as scim_endpoints
from src.modules.digester.extractors.scim.baseline import (
    ConnIdObjectClassDefinition,
    ScimResourceDefinition,
    build_scim_baseline_bundle,
    generate_scim_crud_endpoints,
)
from src.modules.digester.schemas.common import ChunkReference

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


def test_crud_endpoint_generation_requires_explicit_resource_path():
    assert generate_scim_crud_endpoints("", "Device") == []


async def _run_pregenerate(
    object_class: str,
    schemas: dict | None = None,
    object_class_flags: dict | None = None,
    resources: dict | None = None,
    connid_classes: dict | None = None,
) -> dict | None:
    """Invoke pregenerate_scim_endpoints with baseline loading stubbed out."""
    bundle = build_scim_baseline_bundle(
        BASELINE_SCHEMAS if schemas is None else schemas,
        resources,
        connid_classes,
    )

    with (
        patch(f"{_MODULE}.update_job_progress", new_callable=AsyncMock),
        patch(f"{_MODULE}.increment_processed_documents", new_callable=AsyncMock),
        patch(
            f"{_MODULE}.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
    ):
        return await scim_endpoints.pregenerate_scim_endpoints(
            session_id=uuid4(),
            object_class=object_class,
            job_id=uuid4(),
            object_class_flags=object_class_flags,
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
    canonical_name = expected_path.strip("/").removesuffix("s")
    schema = BASELINE_SCHEMAS[canonical_name]
    result = await _run_pregenerate(
        object_class,
        resources={
            canonical_name: ScimResourceDefinition(
                name=canonical_name,
                endpoint=expected_path,
                schema_urn=schema["id"],
                primary_schema=schema,
            )
        },
    )
    assert result is not None
    endpoints = result["result"]["endpoints"]

    assert endpoints, "expected CRUD endpoints for a standard schema-backed resource"
    assert all(ep["path"] in (expected_path, f"{expected_path}/{{id}}") for ep in endpoints)


@pytest.mark.asyncio
async def test_pregenerate_requests_documentation_fallback_for_schema_without_resource_endpoint():
    """A SCIM schema alone does not prove that a standalone CRUD resource exists."""
    schemas = {**BASELINE_SCHEMAS, "Account": {"id": "urn:example:conndev:schemas:Account", "name": "Account"}}
    result = await _run_pregenerate("account", schemas=schemas)

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["embedded", "abstract"])
async def test_pregenerate_skips_non_resource_object_class_flags(flag):
    result = await _run_pregenerate(
        "UserName",
        object_class_flags={flag: True},
    )

    assert result is not None
    assert result["result"]["endpoints"] == []
    assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_pregenerate_skips_extension_schema():
    """SCIM extension schemas (e.g. EnterpriseUser) augment another resource and get no endpoints."""
    result = await _run_pregenerate("enterpriseuser")
    assert result is not None
    assert result["result"]["endpoints"] == []


@pytest.mark.asyncio
async def test_pregenerate_prefers_exported_resource_endpoint_over_inference():
    """The endpoint exported in the conndev resource document beats name-based path inference."""
    resources = {
        "User": ScimResourceDefinition(
            name="User",
            endpoint="/scim/v2/Users",
            schema_urn="urn:ietf:params:scim:schemas:core:2.0:User",
            primary_schema=BASELINE_SCHEMAS["User"],
        )
    }
    result = await _run_pregenerate("user", resources=resources)

    assert result is not None
    endpoints = result["result"]["endpoints"]
    assert endpoints
    assert all(ep["path"] in ("/scim/v2/Users", "/scim/v2/Users/{id}") for ep in endpoints)


@pytest.mark.asyncio
async def test_pregenerate_attaches_exported_resource_provenance():
    reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    resources = {
        "User": ScimResourceDefinition(
            name="User",
            endpoint="/scim/v2/Users",
            schema_urn="urn:ietf:params:scim:schemas:core:2.0:User",
            primary_schema=BASELINE_SCHEMAS["User"],
            source_reference=reference,
        )
    }

    result = await _run_pregenerate("user", resources=resources)

    assert result is not None
    expected_api_reference = reference.to_api_dict()
    assert result["relevantDocumentations"] == [reference.to_internal_dict()]
    assert all(
        endpoint["relevantDocumentations"] == [expected_api_reference] for endpoint in result["result"]["endpoints"]
    )


@pytest.mark.asyncio
async def test_pregenerate_uses_connid_locator_and_its_provenance():
    reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    connid_classes = {
        "Device": ConnIdObjectClassDefinition(
            name="Device",
            namespace="urn:example:Device",
            locator="inventory/devices",
            uid="Device",
            source_reference=reference,
        )
    }

    result = await _run_pregenerate(
        "device",
        schemas={},
        connid_classes=connid_classes,
    )

    assert result is not None
    endpoints = result["result"]["endpoints"]
    assert endpoints
    assert all(endpoint["path"] in {"/inventory/devices", "/inventory/devices/{id}"} for endpoint in endpoints)
    assert result["relevantDocumentations"] == [reference.to_internal_dict()]
    assert all(endpoint["relevantDocumentations"] == [reference.to_api_dict()] for endpoint in endpoints)
