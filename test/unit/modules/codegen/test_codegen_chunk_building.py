# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.core.operations import CreateGenerator


def _generator() -> CreateGenerator:
    return CreateGenerator(
        object_class="User",
        docs_text="Create docs",
        system_prompt="System {total}",
        user_prompt="{chunk}",
        protocol_label="SCIM",
    )


def _documentation_items():
    return [
        {
            "chunkId": "conndev-chunk",
            "content": '{"name":"User","attributes":[]}',
            "metadata": {"content_type": "application/com.evolveum.conndev+json"},
        },
        {
            "chunkId": "provider-chunk",
            "content": "The provider supports filtering by userName.",
            "metadata": {"content_type": "text/html"},
        },
    ]


def test_build_chunks_excludes_conndev_contracts_from_selected_codegen_chunks():
    chunks, provenance, per_chunk_counts, selected_chunk_ids = _generator()._build_chunks(
        _documentation_items(),
        [
            {"chunk_id": "conndev-chunk", "doc_id": "conndev-doc"},
            {"chunk_id": "provider-chunk", "doc_id": "provider-doc"},
        ],
    )

    assert chunks == ["The provider supports filtering by userName."]
    assert provenance == ["provider-chunk"]
    assert per_chunk_counts == {"provider-chunk": 1}
    assert selected_chunk_ids == ["provider-chunk"]


def test_build_chunks_excludes_conndev_contracts_when_codegen_uses_all_documentation():
    chunks, provenance, per_chunk_counts, selected_chunk_ids = _generator()._build_chunks(
        _documentation_items(),
        None,
    )

    assert chunks == ["The provider supports filtering by userName."]
    assert provenance == ["provider-chunk"]
    assert per_chunk_counts == {}
    assert selected_chunk_ids == []
