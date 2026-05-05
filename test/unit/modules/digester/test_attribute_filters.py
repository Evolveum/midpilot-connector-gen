# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.digester.utils.attribute_filters import (
    filter_ignored_attributes,
    ignore_attribute_name,
    normalize_readability_flags,
)


def test_ignore_attribute_name():
    assert ignore_attribute_name("_internal")
    assert ignore_attribute_name("customfield")
    assert ignore_attribute_name("customfield123")
    assert ignore_attribute_name("mail")
    assert ignore_attribute_name("identityurl")
    assert not ignore_attribute_name("displayName")


def test_filter_ignored_attributes():
    attributes = {
        "_private": {"type": "string"},
        "customfield10001": {"type": "string"},
        "mail": {"type": "string"},
        "username": {"type": "string"},
    }

    filtered = filter_ignored_attributes(attributes)

    assert set(filtered.keys()) == {"username"}


def test_normalize_readability_flags():
    attributes = {
        "id": {"readable": True, "returnedByDefault": True},
        "password": {"readable": False, "returnedByDefault": True},
        "token": {"readable": False, "returnedByDefault": None},
    }

    processed = normalize_readability_flags(attributes)

    assert processed["id"]["returnedByDefault"] is True
    assert processed["password"]["returnedByDefault"] is False
    assert processed["token"]["returnedByDefault"] is False
