#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import List, Union

import tiktoken
import yaml

logger = logging.getLogger(__name__)


def encoding(encoding_type: str) -> "tiktoken.Encoding":
    """
    Get the tiktoken encoding for the specified type.
    Types can be found in the tiktoken documentation.
    """
    if not tiktoken:
        raise RuntimeError(
            "The `tiktoken` package is required for token‑aware chunking. Install it with `pip install tiktoken`."
        )
    return tiktoken.get_encoding(encoding_type)


def split_text_with_token_overlap(
    text: str | None, max_tokens: int = 35000, overlap_ratio: float = 0.05, encoding_type: str | None = "cl100k_base"
) -> List[tuple[str, int]]:
    """
    Split *text* into token‑bounded chunks with a configurable overlap.
    inputs:
        text: str - the text to split
        max_tokens: int - maximum number of tokens per chunk
        overlap_ratio: float - ratio of overlap between chunks (0.0 to 0.99)
        encoding_type: str - tiktoken encoding type
    outputs:
        chunks: List[tuple[str,int]] - list of (chunk_text, chunk_token_length)
    """

    if encoding_type is None:
        encoding_type = "cl100k_base"

    if text is None or len(text.strip()) == 0:
        logger.warning("split_text_with_token_overlap called with empty text")
        return []

    if max_tokens <= 0:
        raise ValueError("max_tokens must be > 0")

    # clamp to a sane range to avoid zero/negative steps or full overlap
    overlap_ratio = max(0.0, min(overlap_ratio, 0.9))

    enc = encoding(encoding_type)
    tokens = enc.encode(text)
    chunk_size = max_tokens
    overlap = int(chunk_size * overlap_ratio)
    step = max(1, chunk_size - overlap)

    chunks: List[tuple[str, int]] = []
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start : start + chunk_size]
        chunks.append((enc.decode(chunk_tokens), len(chunk_tokens)))
    return chunks


def normalize_to_text(schema: Union[str, dict, list]) -> str:
    """Return a readable text version of the input spec without double-encoding strings."""
    if isinstance(schema, str):
        return schema
    try:
        return json.dumps(schema, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return yaml.safe_dump(schema, allow_unicode=True)
