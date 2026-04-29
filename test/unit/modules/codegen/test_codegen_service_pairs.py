# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service helper utilities."""

from src.modules.codegen import service
from src.modules.digester.schema import RelationsResponse


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


def test_collect_relation_object_class_pairs_uses_subject_and_object_chunks():
    relations = RelationsResponse.model_validate(
        {
            "relations": [
                {
                    "name": "principal_to_membership",
                    "displayName": "Principal to Membership",
                    "shortDescription": "",
                    "subject": "principal",
                    "subjectAttribute": "memberships",
                    "object": "membership",
                    "objectAttribute": "",
                }
            ]
        }
    )
    object_classes_output = {
        "objectClasses": [
            {
                "name": "Principal",
                "relevantDocumentations": [
                    {"docId": "doc-1", "chunkId": "principal-1"},
                    {"docId": "doc-2", "chunkId": "shared"},
                ],
            },
            {
                "name": "Membership",
                "relevantDocumentations": [
                    {"docId": "doc-2", "chunkId": "shared"},
                    {"docId": "doc-3", "chunkId": "membership-1"},
                ],
            },
            {
                "name": "Role",
                "relevantDocumentations": [{"docId": "doc-4", "chunkId": "role-1"}],
            },
        ]
    }

    assert service._collect_relation_object_class_pairs(relations, object_classes_output) == [
        {"doc_id": "doc-1", "chunk_id": "principal-1"},
        {"doc_id": "doc-2", "chunk_id": "shared"},
        {"doc_id": "doc-3", "chunk_id": "membership-1"},
    ]
