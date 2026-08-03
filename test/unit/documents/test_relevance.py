# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.documents.relevance import extract_attribute_relevance_rows


def test_extract_attribute_relevance_rows_keeps_a_reference_that_names_only_its_chunk():
    """A missing docId must not drop the reference.

    The document a chunk belongs to is resolved from the stored documentation by
    ``RelevantChunkRepository``, so it is not required here.
    """
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
    )

    assert rows == [
        {
            "result_key": "userAttributesOutput",
            "entity_key": "id",
            "doc_id": None,
            "chunk_id": "chunk-1",
            "relevant_sequence": {},
        }
    ]


def test_extract_attribute_relevance_rows_passes_a_claimed_doc_id_through():
    rows = extract_attribute_relevance_rows(
        {
            "attributes": {
                "id": {
                    "type": "string",
                    "relevantDocumentations": [{"chunkId": "chunk-1", "docId": "doc-1"}],
                }
            }
        },
        result_key="userAttributesOutput",
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
