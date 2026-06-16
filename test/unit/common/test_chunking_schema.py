# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json

from src.common.chunking import count_tokens, split_single_item_schema
from src.modules.digester.extractors.sql.schema import collect_sql_tables


def _all_within_budget(chunks: list[tuple[str, int]], max_tokens: int) -> bool:
    return all(token_count <= max_tokens for _, token_count in chunks)


def test_split_sql_keeps_statements_intact_and_within_budget():
    statements = [f"CREATE TABLE t{i} (id bigint primary key, name text);" for i in range(40)]
    text = "\n".join(statements)
    max_tokens = count_tokens(statements[0]) * 5

    chunks = split_single_item_schema(text, parser="text", filename="schema.sql", max_tokens=max_tokens)

    assert len(chunks) > 1
    assert _all_within_budget(chunks, max_tokens)

    # Each chunk is independently parseable and no CREATE TABLE statement was cut.
    recovered = {table["table"] for chunk_text, _ in chunks for table in collect_sql_tables([{"content": chunk_text}])}
    assert recovered == {f"t{i}" for i in range(40)}


def test_split_sql_ignores_semicolons_inside_strings_and_comments():
    text = (
        "CREATE TABLE a (id int, note text DEFAULT 'has ; semicolon');\n"
        "-- a comment with ; inside\n"
        "CREATE TABLE b (id int);\n"
    )
    # Budget large enough to keep everything together: must stay one chunk, one valid statement set.
    chunks = split_single_item_schema(text, parser="text", filename="schema.sql", max_tokens=count_tokens(text) + 10)

    assert len(chunks) == 1
    tables = {table["table"] for table in collect_sql_tables([{"content": chunks[0][0]}])}
    assert tables == {"a", "b"}


def test_split_json_array_yields_valid_json_arrays_within_budget():
    tables = [{"table": f"t{i}", "columns": [{"name": "id", "type": "bigint"}]} for i in range(30)]
    text = json.dumps(tables, indent=2)
    max_tokens = count_tokens(json.dumps(tables[0])) * 4

    chunks = split_single_item_schema(text, parser="json", filename="schema.json", max_tokens=max_tokens)

    assert len(chunks) > 1
    assert _all_within_budget(chunks, max_tokens)
    for chunk_text, _ in chunks:
        parsed = json.loads(chunk_text)  # raises if invalid
        assert isinstance(parsed, list)

    recovered = {table["table"] for chunk_text, _ in chunks for table in collect_sql_tables([{"content": chunk_text}])}
    assert recovered == {f"t{i}" for i in range(30)}


def test_split_json_dict_preserves_container_key_for_oversized_value():
    tables = [{"table": f"t{i}", "columns": [{"name": "id", "type": "bigint"}]} for i in range(30)]
    schema = {"databaseName": "app", "tables": tables}
    text = json.dumps(schema, indent=2)
    max_tokens = count_tokens(json.dumps(tables[0])) * 4

    chunks = split_single_item_schema(text, parser="json", filename="schema.json", max_tokens=max_tokens)

    assert len(chunks) > 1
    for chunk_text, _ in chunks:
        json.loads(chunk_text)  # every chunk is valid JSON

    # The "tables" container key survives the split so the heuristic still finds tables.
    recovered = {table["table"] for chunk_text, _ in chunks for table in collect_sql_tables([{"content": chunk_text}])}
    assert recovered == {f"t{i}" for i in range(30)}


def test_split_invalid_json_falls_back_without_raising():
    text = "{ not valid json " * 5000
    max_tokens = 500

    chunks = split_single_item_schema(text, parser="json", filename="broken.json", max_tokens=max_tokens)

    assert len(chunks) > 1
