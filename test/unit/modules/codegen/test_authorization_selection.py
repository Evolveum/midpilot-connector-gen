# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.selection.authorization import (
    ANALYSIS_SUPPORT_FIELD,
    ANALYSIS_SUPPORT_UNSUPPORTED,
    prepare_preferred_authorizations_for_generation,
    select_authorization_chunk_refs,
)


def test_select_authorization_chunk_refs_returns_empty_for_unmatched_midpoint_authorization() -> None:
    auth_payload = {
        "auth": [
            {
                "name": "AI discovered bearer token",
                "type": "bearer",
                "relevant_sequences": [{"chunk_id": "chunk-bearer"}],
            }
        ]
    }
    relevant_documentations = {"authOutput": [{"docId": "doc-1", "chunkId": "chunk-bearer"}]}
    preferred_authorizations = [{"name": "HTTP JWT Bearer Token Authorization", "type": "jwtBearer"}]

    assert select_authorization_chunk_refs(relevant_documentations, auth_payload, preferred_authorizations) == []


def test_select_authorization_chunk_refs_keeps_supported_chunks_when_mixed_with_unmatched_authorization() -> None:
    auth_payload = {
        "auth": [
            {
                "name": "Bearer token",
                "type": "bearer",
                "relevant_sequences": [{"chunk_id": "chunk-bearer"}],
            },
            {
                "name": "Basic authentication",
                "type": "basic",
                "relevant_sequences": [{"chunk_id": "chunk-basic"}],
            },
        ]
    }
    relevant_documentations = {
        "authOutput": [
            {"docId": "doc-1", "chunkId": "chunk-bearer"},
            {"docId": "doc-2", "chunkId": "chunk-basic"},
        ]
    }
    preferred_authorizations = [
        {"name": "Bearer token", "type": "bearer"},
        {"name": "HTTP JWT Bearer Token Authorization", "type": "jwtBearer"},
    ]

    assert select_authorization_chunk_refs(relevant_documentations, auth_payload, preferred_authorizations) == [
        {"doc_id": "doc-1", "chunk_id": "chunk-bearer"}
    ]


def test_prepare_preferred_authorizations_for_generation_marks_unmatched_authorizations() -> None:
    prepared = prepare_preferred_authorizations_for_generation(
        {"auth": []},
        [{"name": "HTTP JWT Bearer Token Authorization", "type": "jwtBearer", "quirks": ""}],
    )

    assert prepared == [
        {
            "name": "HTTP JWT Bearer Token Authorization",
            "type": "jwtBearer",
            "quirks": (
                "Selected in midPoint, but this authentication method was not identified in the analyzed application "
                "documentation. No application-specific authorization customization can be generated."
            ),
            ANALYSIS_SUPPORT_FIELD: ANALYSIS_SUPPORT_UNSUPPORTED,
        }
    ]
