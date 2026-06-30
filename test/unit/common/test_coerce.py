# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the defensive coercion helpers."""

from collections import OrderedDict

from src.common.utils.coerce import as_list, as_mapping, as_str, as_str_list


def test_as_mapping_passes_through_mappings():
    assert as_mapping({"a": 1}) == {"a": 1}
    assert as_mapping(OrderedDict(a=1)) == {"a": 1}


def test_as_mapping_defaults_for_non_mappings():
    for bad in (None, [], "x", 5, ("a", "b")):
        assert as_mapping(bad) == {}


def test_as_list_passes_through_lists_only():
    assert as_list([1, 2]) == [1, 2]
    # tuples/sets/strings are not lists -> empty default (no surprise char iteration)
    for bad in (None, "scim", ("a",), {"a"}, {"a": 1}):
        assert as_list(bad) == []


def test_as_str_passes_through_strings_only():
    assert as_str("hi") == "hi"
    for bad in (None, 123, [], {}):
        assert as_str(bad) == ""


def test_as_str_list_keeps_only_strings():
    assert as_str_list(["a", 1, None, "b"]) == ["a", "b"]
    # a bare string is not a list of strings
    assert as_str_list("scim") == []
    assert as_str_list(None) == []
