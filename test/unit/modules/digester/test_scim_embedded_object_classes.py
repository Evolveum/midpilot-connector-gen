# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim.object_class import (
    build_embedded_object_class_name,
    extract_scim_object_classes,
    get_embedded_object_classes_from_scim_schema,
)

_SCHEMA_DIR = Path(__file__).parent / "scim_schemas"


def _baseline_schemas() -> dict:
    """Load the SCIM baseline schemas that stand in for a session's conndev documents."""
    schemas: dict = {}
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        schemas[schema["name"]] = schema
    return schemas


BASELINE_SCHEMAS = _baseline_schemas()


def test_build_embedded_object_class_name_preserves_schema_attribute_plurality():
    assert build_embedded_object_class_name("User", "phoneNumbers") == "UserPhoneNumbers"
    assert build_embedded_object_class_name("User", "addresses") == "UserAddresses"
    assert build_embedded_object_class_name("User", "ims") == "UserIms"
    assert build_embedded_object_class_name("Group", "members") == "GroupMembers"
    assert build_embedded_object_class_name("User", "name") == "UserName"


def test_get_embedded_object_classes_from_scim_schema_uses_complex_attributes_only():
    schema = {
        "attributes": [
            {
                "name": "userName",
                "type": "string",
                "description": "Unique identifier for the User.",
            },
            {
                "name": "phoneNumbers",
                "type": "complex",
                "multiValued": True,
                "description": "Phone numbers for the User.",
                "subAttributes": [{"name": "value", "type": "string"}],
            },
        ]
    }

    embedded_classes = get_embedded_object_classes_from_scim_schema("User", schema)

    assert embedded_classes == [
        {
            "name": "UserPhoneNumbers",
            "superclass": None,
            "abstract": False,
            "embedded": True,
            "description": "Phone numbers for the User.",
            "sourceAttribute": "phoneNumbers",
        }
    ]


@pytest.mark.asyncio
async def test_extract_scim_object_classes_includes_standard_embedded_classes():
    with (
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.scim.object_class.run_chunks_concurrently", new_callable=AsyncMock
        ) as run_chunks,
        patch(
            "src.modules.digester.extractors.scim.object_class.load_session_scim_schemas",
            new_callable=AsyncMock,
            return_value=BASELINE_SCHEMAS,
        ),
    ):
        run_chunks.return_value = []

        result = await extract_scim_object_classes([], uuid4(), uuid4())

    object_classes = result["result"]["objectClasses"]
    by_name = {item["name"]: item for item in object_classes}

    assert by_name["User"]["embedded"] is False
    assert by_name["EnterpriseUser"]["superclass"] == "User"
    assert by_name["UserName"]["embedded"] is True
    assert by_name["UserName"]["superclass"] is None
    assert by_name["UserPhoneNumbers"]["embedded"] is True
    assert by_name["UserPhoneNumbers"]["superclass"] is None
    assert by_name["GroupMembers"]["embedded"] is True
    assert by_name["GroupMembers"]["superclass"] is None
