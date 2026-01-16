#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import json
import logging
import re
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


def get_neighboring_tokens(
    search_phrase: str,
    text: str,
    context_token_count_before: int = 150,
    context_token_count_after: int = 1000,
    encoding_type: str = "cl100k_base",
    word_boundary: bool = True,
) -> str:
    """
    Extract one or more snippets from the text that contain the search phrase along with a specified number of tokens before and after it.
    inputs:
        search_phrase: str - phrase to search for in the text
        text: str - the text to search within
        context_token_count_before: int - number of tokens to include before every occurance of the search phrase
        context_token_count_after: int - number of tokens to include after every occurance of the search phrase
        encoding_type: str - tiktoken encoding type
        word_boundary: bool - if True, only match if phrase is followed by whitespace/newline/punctuation
    outputs:
        snippet: str - concatenated snippets containing the search phrase with surrounding context
    """
    if not text or not search_phrase:
        return ""
    enc = encoding(encoding_type)
    parts = []
    # We must use re because with tokenized text, there were weird bugs
    if word_boundary:
        parts = re.split(r"(" + re.escape(search_phrase) + r'[\s\n\t.,;:!?\-\)\]\}"\'])', text, flags=re.IGNORECASE)
    else:
        parts = re.split("(" + re.escape(search_phrase) + ")", text, flags=re.IGNORECASE)
    if len(parts) == 1:
        return ""
    snippets = []
    for i in range(1, len(parts), 2):
        before = parts[i - 1]
        after = parts[i + 1]
        before_tokens = enc.encode(before)
        after_tokens = enc.encode(after)
        start_index = max(0, len(before_tokens) - context_token_count_before)
        end_index = min(len(after_tokens), context_token_count_after)
        snippet = enc.decode(before_tokens[start_index:]) + parts[i] + enc.decode(after_tokens[:end_index])
        snippets.append(snippet.strip())

    return "\n...\n".join(snippets)


def normalize_to_text(schema: Union[str, dict, list]) -> str:
    """Return a readable text version of the input spec without double-encoding strings."""
    if isinstance(schema, str):
        return schema
    try:
        return json.dumps(schema, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return yaml.safe_dump(schema, allow_unicode=True)
