# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.shared.content_types import (
    detect_conndev_api_type,
    detect_conndev_object_class_api_type,
    get_documentation_item_content_type,
    is_conndev_documentation_item,
    is_conndev_object_class_document,
)
from src.shared.enums import ApiType


@pytest.mark.parametrize(
    "item",
    [
        {"@metadata": {"content_type": "application/com.evolveum.conndev+json"}},
        {"metadata": {"content_type": " application/conndev+json; charset=utf-8 "}},
        {
            "@metadata": {},
            "metadata": {"content_type": "APPLICATION/COM.EVOLVEUM.CONNDEV+JSON"},
        },
    ],
)
def test_conndev_documentation_item_supports_normalized_and_repository_shapes(item):
    assert is_conndev_documentation_item(item)


def test_documentation_item_content_type_ignores_invalid_metadata_shapes():
    assert get_documentation_item_content_type({"@metadata": "invalid"}) is None
    assert not is_conndev_documentation_item({"@metadata": {"content_type": "application/json"}})


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"uid": "Account", "sql": {}}, ApiType.SQL),
        ({"uid": "User", "scim": {}}, ApiType.SCIM),
        ({"uid": "User", "locator": "/Users"}, ApiType.SCIM),
        ({"schemaContent": "{}", "name": "User"}, ApiType.SCIM),
        ({"endpoint": "/Users", "primarySchema": "{}"}, ApiType.SCIM),
        ({"name": "ServiceProviderConfig", "content": "{}"}, ApiType.SCIM),
        ({"something": "else"}, None),
        # Embedded sub-class exports carry the binding only on their attribute shadows.
        (
            {
                "uid": "User__name",
                "name": "User__name",
                "attributes": [
                    {"object": {"objectClass": "ri:conndev_Attribute", "attributes": {"scim": {"path": "givenName"}}}}
                ],
            },
            ApiType.SCIM,
        ),
        (
            {
                "uid": "Account__address",
                "name": "Account__address",
                "attributes": {"object": {"attributes": {"sql": {"column": "street"}}}},
            },
            ApiType.SQL,
        ),
        # An embedded sub-class without attributes declares no protocol anywhere.
        ({"uid": "Entitlement__typeInfo", "name": "Entitlement__typeInfo"}, None),
    ],
)
def test_detect_conndev_api_type_supports_all_known_contract_shapes(document, expected):
    assert detect_conndev_api_type(document) is expected


def test_detect_conndev_object_class_api_type_only_accepts_bound_object_classes():
    assert detect_conndev_object_class_api_type({"uid": "Account", "sql": {}}) is ApiType.SQL
    assert detect_conndev_object_class_api_type({"schemaContent": "{}"}) is None


def test_conndev_object_class_document_covers_bound_and_embedded_exports():
    assert is_conndev_object_class_document({"uid": "User", "name": "User", "scim": {}})
    assert is_conndev_object_class_document({"uid": "Entitlement__typeInfo", "name": "Entitlement__typeInfo"})
    assert not is_conndev_object_class_document({"schemaContent": "{}", "name": "User"})
    assert not is_conndev_object_class_document({"uid": " ", "name": "User"})
    assert not is_conndev_object_class_document(None)
