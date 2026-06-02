# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.scim.object_class import extract_scim_object_classes
from src.modules.digester.scim.embedded import (
    build_embedded_object_class_name,
    get_embedded_object_classes_from_scim_schema,
)


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
            "superclass": "User",
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
    ):
        run_chunks.return_value = []

        result = await extract_scim_object_classes([], uuid4())

    object_classes = result["result"]["objectClasses"]
    by_name = {item["name"]: item for item in object_classes}

    assert by_name["User"]["embedded"] is False
    assert by_name["UserName"]["embedded"] is True
    assert by_name["UserName"]["superclass"] == "User"
    assert by_name["UserPhoneNumbers"]["embedded"] is True
    assert by_name["UserPhoneNumbers"]["superclass"] == "User"
    assert by_name["GroupMembers"]["embedded"] is True
    assert by_name["GroupMembers"]["superclass"] == "Group"
