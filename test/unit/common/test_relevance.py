# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.utils.relevance import extract_attribute_relevance_rows


def test_extract_attribute_relevance_rows_resolves_doc_id_from_chunk_map():
    rows = extract_attribute_relevance_rows(
        {
            "attributes": {
                "id": {
                    "type": "string",
                    "relevantDocumentations": [{"chunkId": "chunk-1"}],
                }
            }
        },
        result_key="userAttributesOutput",
        chunk_to_doc={"chunk-1": "doc-1"},
    )

    assert rows == [
        {
            "result_key": "userAttributesOutput",
            "entity_key": "id",
            "doc_id": "doc-1",
            "chunk_id": "chunk-1",
            "relevant_sequence": {},
        }
    ]
