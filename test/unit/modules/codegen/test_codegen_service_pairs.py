# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service helper utilities."""

from src.modules.codegen import service


def test_collect_pairs_new_format():
    """Test _collect_pairs with format containing chunk_id."""
    input_data = [
        {"chunk_id": "uuid1"},
        {"chunk_id": "uuid2"},
        {"chunk_id": "uuid3"},
    ]

    result = service._collect_pairs(input_data)

    expected = [(0, "uuid1"), (1, "uuid2"), (2, "uuid3")]
    assert result == expected


def test_collect_pairs_legacy_format():
    """Test _collect_pairs with legacy format containing only integers."""
    # Legacy format: list of integers
    input_data = [1, 2, 3, 4]

    result = service._collect_pairs(input_data)

    expected = [(1, None), (2, None), (3, None), (4, None)]
    assert result == expected


def test_collect_pairs_empty_input():
    """Test _collect_pairs with empty or None input."""
    assert service._collect_pairs(None) == []
    assert service._collect_pairs([]) == []
    assert service._collect_pairs("") == []
