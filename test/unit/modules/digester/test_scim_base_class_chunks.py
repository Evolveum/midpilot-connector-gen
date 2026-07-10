# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace

import pytest

from src.modules.digester.extractors.scim.object_class import _find_relevant_chunks_for_base_classes


@pytest.mark.asyncio
async def test_base_class_chunk_search_tolerates_non_string_content():
    """Chunk content that is None or a non-string must not crash the search.

    Regression: ``doc_item.get("content", "").lower()`` raised AttributeError
    when the content key was present but None (or a dict/list), crashing the
    SCIM getObjectClass job.
    """
    base_classes = [SimpleNamespace(name="User")]
    doc_items = [
        {"content": None, "chunkId": "c1", "docId": "d1"},
        {"content": {"nested": "User resource"}, "chunkId": "c2", "docId": "d2"},
        {"content": "The User endpoint returns users", "chunkId": "c3", "docId": "d3"},
    ]
    class_to_chunks: dict = {}

    # Must not raise.
    await _find_relevant_chunks_for_base_classes(base_classes, doc_items, class_to_chunks)

    # The plain-text chunk that mentions the User endpoint is still matched.
    refs = class_to_chunks.get("user", [])
    assert {"doc_id": "d3", "chunk_id": "c3"} in refs
