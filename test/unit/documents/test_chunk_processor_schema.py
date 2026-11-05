# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.documents.processing.schema import LlmChunkOutput


def test_llm_chunk_output_normalizes_nullish_num_endpoints_to_zero() -> None:
    output = LlmChunkOutput.model_validate(
        {
            "summary": "A JSON API spec fragment.",
            "num_endpoints": None,
            "tags": ["REST"],
            "category": "spec_json",
            "num_defined_object_classes": None,
        }
    )

    assert output.num_endpoints == 0


def test_llm_chunk_output_normalizes_string_null_num_endpoints_to_zero() -> None:
    output = LlmChunkOutput.model_validate(
        {
            "summary": "A JSON API spec fragment.",
            "num_endpoints": "null",
            "tags": ["REST"],
            "category": "spec_json",
            "num_defined_object_classes": None,
        }
    )

    assert output.num_endpoints == 0
