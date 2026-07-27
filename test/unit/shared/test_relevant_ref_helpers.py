# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.documents.normalize import canonicalize_scim_path, normalize_scim_path_for_lookup
from src.shared.normalize import build_relevant_documentations, normalize_relevant_sequence


def test_normalize_relevant_sequence_accepts_both_casings():
    assert normalize_relevant_sequence({"startSequence": "a", "endSequence": "b"}) == {
        "startSequence": "a",
        "endSequence": "b",
    }
    assert normalize_relevant_sequence({"start_sequence": "a", "end_sequence": "b"}) == {
        "startSequence": "a",
        "endSequence": "b",
    }


def test_normalize_relevant_sequence_returns_empty_when_incomplete():
    assert normalize_relevant_sequence({"startSequence": "a"}) == {}
    assert normalize_relevant_sequence(None) == {}
    assert normalize_relevant_sequence("not-a-mapping") == {}


def test_build_relevant_documentations_sorts_and_dedupes():
    pairs = {("d2", "c2"), ("d1", "c1"), ("d1", "c1")}
    assert build_relevant_documentations(pairs) == [
        {"docId": "d1", "chunkId": "c1"},
        {"docId": "d2", "chunkId": "c2"},
    ]


def test_build_relevant_documentations_empty():
    assert build_relevant_documentations(set()) == []


def test_scim_path_normalization_converts_quoted_bracket_subattributes():
    assert canonicalize_scim_path("emails[0]['value']") == "emails[0].value"
    assert canonicalize_scim_path('addresses[primary]["locality"]') == "addresses[primary].locality"
    assert normalize_scim_path_for_lookup("emails[0]['value']") == "emails.value"
