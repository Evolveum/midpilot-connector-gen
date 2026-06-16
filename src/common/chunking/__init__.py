# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Chunking utilities.

Single home for splitting text and schemas into LLM-sized chunks:

- ``tokens``: token counting and token-budget text splitting.
- ``schema``: structure-aware splitting that keeps each chunk valid JSON/YAML/SQL.
"""

from src.common.chunking.schema import split_single_item_schema
from src.common.chunking.tokens import (
    count_tokens,
    encoding,
    get_neighboring_tokens,
    normalize_to_text,
    split_text_with_token_overlap,
)

__all__ = [
    "count_tokens",
    "encoding",
    "get_neighboring_tokens",
    "normalize_to_text",
    "split_text_with_token_overlap",
    "split_single_item_schema",
]
