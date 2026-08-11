# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.documents.relevance import (
    extract_attribute_relevance_rows,
    extract_relevant_rows_for_storage,
    hydrate_auth_sequences_from_relevance,
)


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


def test_auth_relevance_rows_use_the_canonical_authentication_identity():
    rows = extract_relevant_rows_for_storage(
        {
            "result": {
                "auth": [
                    {
                        "name": "Bearer Token",
                        "type": "jwt-bearer",
                        "relevantSequences": [
                            {
                                "chunkId": "chunk-1",
                                "startSequence": "Bearer authentication",
                                "endSequence": "JWT validation",
                            }
                        ],
                    }
                ]
            }
        },
        result_key="authOutput",
    )

    assert rows == [
        {
            "result_key": "authOutput",
            "entity_key": "bearertoken|jwtbearer",
            "doc_id": None,
            "chunk_id": "chunk-1",
            "relevant_sequence": {
                "startSequence": "Bearer authentication",
                "endSequence": "JWT validation",
            },
        }
    ]


@pytest.mark.asyncio
async def test_auth_relevance_hydration_matches_equivalent_name_and_type_variants():
    repo = MagicMock()
    repo.get_relevant_chunks_grouped_by_entity = AsyncMock(
        return_value={
            "bearertoken|jwtbearer": [
                {
                    "docId": "doc-1",
                    "chunkId": "chunk-1",
                    "relevantSequence": {
                        "startSequence": "Bearer authentication",
                        "endSequence": "JWT validation",
                    },
                }
            ]
        }
    )

    with patch("src.documents.relevance.persistence.RelevantChunkRepository", return_value=repo):
        hydrated = await hydrate_auth_sequences_from_relevance(
            MagicMock(),
            uuid4(),
            {"auth": [{"name": "bearer-token", "type": "jwtBearer"}]},
        )

    assert hydrated["auth"][0]["relevant_sequences"] == [
        {
            "chunk_id": "chunk-1",
            "start_sequence": "Bearer authentication",
            "end_sequence": "JWT validation",
        }
    ]
