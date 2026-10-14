# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch
from uuid import uuid4

import pytest
import yaml

from src.modules.codegen import generation
from src.modules.codegen.utils.connector_code_validation import validate_connector_code
from src.modules.codegen.utils.object_class_casing import restore_object_class_casing
from src.shared.enums import ApiType


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('objectClass("Group") {\n  search {}\n}', 'objectClass("group") {\n  search {}\n}'),
        ("objectClass( 'Group' ) {}", "objectClass( 'group' ) {}"),
        ('// generated\nobjectClass("GROUP") {\n}', '// generated\nobjectClass("group") {\n}'),
    ],
)
def test_groovy_declaration_takes_the_requested_spelling(code, expected):
    assert restore_object_class_casing(code, "group") == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("objectClasses:\n  Group:\n    search: {}\n", "objectClasses:\n  group:\n    search: {}\n"),
        ('objectClasses:\n  "Group":\n    search: {}', 'objectClasses:\n  "group":\n    search: {}'),
        ("objectClasses:\n  'Group':\n    search: {}", "objectClasses:\n  'group':\n    search: {}"),
        ("objectClasses: {Group: {search: {}}}", "objectClasses: {group: {search: {}}}"),
        ("# comment kept\nobjectClasses:\n  Group: {}\n", "# comment kept\nobjectClasses:\n  group: {}\n"),
    ],
)
def test_yaml_class_key_takes_the_requested_spelling(code, expected):
    restored = restore_object_class_casing(code, "group")
    assert restored == expected
    assert validate_connector_code(restored) is None


def test_yaml_attribute_sharing_the_class_name_is_not_renamed():
    code = (
        "objectClasses:\n  User:\n    attributes:\n      Group:\n        jsonType: string\n  Group:\n    search: {}\n"
    )
    restored = restore_object_class_casing(code, "group")
    assert yaml.safe_load(restored) == {
        "objectClasses": {"User": {"attributes": {"Group": {"jsonType": "string"}}}, "group": {"search": {}}}
    }


def test_requested_name_that_needs_quoting_is_quoted():
    restored = restore_object_class_casing("objectClasses:\n  Service Account: {}\n", "service account")
    assert yaml.safe_load(restored) == {"objectClasses": {"service account": {}}}


@pytest.mark.parametrize(
    "code",
    [
        "",
        'objectClass("group") {}',
        'objectClass("User") {}',
        "objectClasses:\n  group: {}\n  Group: {}\n",
        "objectClasses:\n  User:\n    search: {}\n",
        "objectClasses:\n  group:\n    search: {}\n",
        "authentication:\n  scim: {}\n",
        "invalid: [",
    ],
)
def test_exact_other_or_ambiguous_names_are_left_alone(code):
    assert restore_object_class_casing(code, "group") == code


@pytest.mark.parametrize("object_class", ['gro"up', "gro$up", "gro\\up", ""])
def test_unsafe_requested_name_is_never_written(object_class):
    code = 'objectClass("GROUP") {}'
    assert restore_object_class_casing(code, object_class) == code


@pytest.mark.asyncio
async def test_native_schema_keeps_the_requested_spelling():
    with patch("src.modules.codegen.generation.generate_groovy", return_value='objectClass("Group") {\n}'):
        result = await generation.generate_native_schema_code(
            {"name": {"type": "string"}},
            "group",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )

    assert result["code"] == 'objectClass("group") {\n}'
