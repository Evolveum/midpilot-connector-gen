# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.common.documentation.content_types import (
    get_documentation_item_content_type,
    is_conndev_documentation_item,
)


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
