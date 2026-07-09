# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Regression tests for the ``Documentation`` API model behaviour that the
``PUT /session/{id}/documentation/{doc_id}`` import endpoint relies on to
distinguish a schema mismatch from an intentionally empty import (and to log it
instead of silently returning 200 with nothing stored)."""

from src.common.session.schema import Documentation


def test_mismatched_payload_yields_no_chunks_and_captures_unexpected_keys() -> None:
    """A conndev object-class document has none of the expected fields; it must
    parse into an empty document while preserving the unexpected keys so the
    import endpoint can report why 0 chunks were stored."""
    conndev_payload = {
        "namespace": "urn:ietf:params:scim:schemas:core:2.0:Group",
        "attributes": [{"name": "displayName", "type": "string"}],
        "locator": "/Groups",
        "name": "Group",
        "uid": "Group",
    }

    document = Documentation.model_validate(conndev_payload)

    assert document.chunks == []
    assert "chunks" not in document.model_fields_set
    assert sorted((document.model_extra or {}).keys()) == [
        "attributes",
        "locator",
        "name",
        "namespace",
        "uid",
    ]


def test_explicit_empty_chunks_is_distinguishable_from_mismatch() -> None:
    """An intentional empty import sets ``chunks`` explicitly, which lets the
    endpoint log a different (less alarming) message than a schema mismatch."""
    document = Documentation.model_validate({"chunks": []})

    assert document.chunks == []
    assert "chunks" in document.model_fields_set
    assert not (document.model_extra or {})


def test_valid_chunk_bundle_parses_chunks() -> None:
    document = Documentation.model_validate(
        {
            "docId": "11111111-1111-1111-1111-111111111111",
            "chunks": [
                {
                    "chunkId": "22222222-2222-2222-2222-222222222222",
                    "source": "upload",
                    "content": "hello",
                    "createdAt": "2026-07-09T00:00:00Z",
                }
            ],
        }
    )

    assert len(document.chunks) == 1
    assert document.chunks[0].content == "hello"
