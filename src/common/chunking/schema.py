# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Structure-aware splitting of oversized single-item schemas.

Some schemas (SQL DDL, SCIM/conndev JSON or YAML) are intentionally kept as a
single documentation item because downstream heuristics parse them in their
native format (runnable SQL statements, valid JSON/YAML). A naive token-overlap
split would cut through the middle of a statement or object and break that
parsing.

When such a schema is too large for the LLM chunk-processing step, this module
splits it into multiple sub-schemas that are each:

- independently valid in the schema's native format (so the heuristics still
  work; they already merge results across documentation items), and
- within the configured token budget (so the LLM context window is respected).

Only the splitting logic lives here; deciding *when* to split and wiring the
result into the upload pipeline stays in the caller.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import yaml

from src.common.chunking.tokens import count_tokens, split_text_with_token_overlap

logger = logging.getLogger(__name__)

Serializer = Callable[[Any], str]


def split_single_item_schema(
    text: str,
    *,
    parser: str,
    filename: str,
    max_tokens: int,
) -> list[tuple[str, int]]:
    """Split an oversized single-item schema into valid sub-schemas under ``max_tokens``.

    Args:
        text: The full schema text (already pretty-printed by the parser stage).
        parser: The parser used for the upload (``json``, ``yaml`` or ``text`` for SQL).
        filename: Source filename, used for logging only.
        max_tokens: Per-chunk token budget for the schema content.

    Returns:
        A list of ``(chunk_text, token_count)`` tuples, each independently valid
        in the schema's native format and within ``max_tokens`` where possible.
    """
    if parser == "json":
        return _split_structured_schema(
            text, filename=filename, max_tokens=max_tokens, serialize=_serialize_json, load=json.loads
        )
    if parser == "yaml":
        return _split_structured_schema(
            text, filename=filename, max_tokens=max_tokens, serialize=_serialize_yaml, load=yaml.safe_load
        )
    return _split_sql_statements(text, filename=filename, max_tokens=max_tokens)


def _serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _serialize_yaml(value: Any) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)


def _as_chunks(texts: list[str]) -> list[tuple[str, int]]:
    return [(stripped, count_tokens(stripped)) for text in texts if (stripped := text.strip())]


# --------------------------------------------------------------------------- #
# JSON / YAML structural splitting
# --------------------------------------------------------------------------- #


def _split_structured_schema(
    text: str,
    *,
    filename: str,
    max_tokens: int,
    serialize: Serializer,
    load: Callable[[str], Any],
) -> list[tuple[str, int]]:
    try:
        parsed = load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        logger.warning(
            "[Chunking] Single-item schema %s is not valid structured data (%s); "
            "falling back to token-overlap split which may break native parsing.",
            filename,
            exc,
        )
        return split_text_with_token_overlap(text, max_tokens=max_tokens, overlap_ratio=0.0)

    values = _split_value(parsed, max_tokens=max_tokens, serialize=serialize)
    return _as_chunks([serialize(value) for value in values])


def _split_value(value: Any, *, max_tokens: int, serialize: Serializer) -> list[Any]:
    """Split a parsed value into sub-values that each serialize within ``max_tokens``.

    Each returned sub-value is a valid structural fragment (list, dict or scalar),
    so re-serializing it yields valid JSON/YAML.
    """
    if count_tokens(serialize(value)) <= max_tokens:
        return [value]
    if isinstance(value, list):
        return _split_list_value(value, max_tokens=max_tokens, serialize=serialize)
    if isinstance(value, dict):
        return _split_dict_value(value, max_tokens=max_tokens, serialize=serialize)
    if isinstance(value, str):
        return _split_string_value(value, max_tokens=max_tokens)
    logger.warning("[Chunking] Oversized scalar schema value cannot be split further; emitting as-is.")
    return [value]


def _split_list_value(items: list[Any], *, max_tokens: int, serialize: Serializer) -> list[Any]:
    groups: list[Any] = []
    current: list[Any] = []
    current_tokens = 0

    for item in items:
        item_tokens = count_tokens(serialize(item))
        if item_tokens > max_tokens:
            if current:
                groups.append(current)
                current, current_tokens = [], 0
            # Keep the list wrapper around each split fragment so the shape is preserved.
            groups.extend([sub] for sub in _split_value(item, max_tokens=max_tokens, serialize=serialize))
            continue
        if current and current_tokens + item_tokens > max_tokens:
            groups.append(current)
            current, current_tokens = [], 0
        current.append(item)
        current_tokens += item_tokens

    if current:
        groups.append(current)
    return groups


def _split_dict_value(value: dict[str, Any], *, max_tokens: int, serialize: Serializer) -> list[Any]:
    oversized: dict[str, Any] = {}
    small: dict[str, Any] = {}
    for key, val in value.items():
        if count_tokens(serialize({key: val})) > max_tokens:
            oversized[key] = val
        else:
            small[key] = val

    # Scalar metadata (e.g. databaseName, schema version) is cheap and identifies the
    # schema. When a container key has to be split, carry that metadata into every
    # fragment so each sub-schema stays self-describing and so the metadata is not
    # emitted as a lone object that downstream heuristics could misread.
    scalar_context = {key: val for key, val in small.items() if not isinstance(val, (list, dict))} if oversized else {}
    context_tokens = count_tokens(serialize(scalar_context)) if scalar_context else 0
    standalone_small = {key: val for key, val in small.items() if key not in scalar_context}

    groups: list[Any] = []
    current: dict[str, Any] = {}
    current_tokens = 0
    for key, val in standalone_small.items():
        entry_tokens = count_tokens(serialize({key: val}))
        if current and current_tokens + entry_tokens > max_tokens:
            groups.append(current)
            current, current_tokens = {}, 0
        current[key] = val
        current_tokens += entry_tokens
    if current:
        groups.append(current)

    fragment_budget = max(1, max_tokens - context_tokens)
    for key, val in oversized.items():
        # Re-wrap each fragment under the original key so container keys
        # (e.g. "tables", "schema") are preserved for downstream heuristics.
        for sub in _split_value(val, max_tokens=fragment_budget, serialize=serialize):
            groups.append({**scalar_context, key: sub})

    return groups


def _split_string_value(text: str, *, max_tokens: int) -> list[Any]:
    parts = split_text_with_token_overlap(text, max_tokens=max_tokens, overlap_ratio=0.0)
    return [part for part, _ in parts]


# --------------------------------------------------------------------------- #
# SQL statement splitting
# --------------------------------------------------------------------------- #


def _split_sql_statements(text: str, *, filename: str, max_tokens: int) -> list[tuple[str, int]]:
    statements = _iter_sql_statements(text)
    groups: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for statement in statements:
        statement_tokens = count_tokens(statement)
        if statement_tokens > max_tokens:
            if current:
                groups.append("\n".join(current))
                current, current_tokens = [], 0
            logger.warning(
                "[Chunking] Single SQL statement in %s exceeds the token budget; "
                "token-splitting it, which may break native SQL parsing for that statement.",
                filename,
            )
            groups.extend(
                part for part, _ in split_text_with_token_overlap(statement, max_tokens=max_tokens, overlap_ratio=0.0)
            )
            continue
        if current and current_tokens + statement_tokens > max_tokens:
            groups.append("\n".join(current))
            current, current_tokens = [], 0
        current.append(statement)
        current_tokens += statement_tokens

    if current:
        groups.append("\n".join(current))
    return _as_chunks(groups)


def _iter_sql_statements(text: str) -> list[str]:
    """Split SQL into complete statements, terminating on top-level ``;``.

    String literals (``'`` ``"`` ``` ` ```) and comments (``--`` line, ``/* */``
    block) are tracked so semicolons inside them do not split a statement.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_single = in_double = in_back = in_line_comment = in_block_comment = False

    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        buffer.append(char)

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
        elif in_block_comment:
            if char == "*" and nxt == "/":
                buffer.append(nxt)
                index += 2
                in_block_comment = False
                continue
        elif in_single:
            if char == "'":
                in_single = False
        elif in_double:
            if char == '"':
                in_double = False
        elif in_back:
            if char == "`":
                in_back = False
        elif char == "-" and nxt == "-":
            in_line_comment = True
        elif char == "/" and nxt == "*":
            buffer.append(nxt)
            index += 2
            in_block_comment = True
            continue
        elif char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == "`":
            in_back = True
        elif char == ";":
            statements.append("".join(buffer))
            buffer = []

        index += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements
