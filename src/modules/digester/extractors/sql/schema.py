# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import re
from collections import OrderedDict
from typing import Any, Iterable

from src.common.chunking import normalize_to_text
from src.modules.digester.selection import build_chunk_references_from_doc_items

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:TEMPORARY\s+|TEMP\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<name>(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w.]+))\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_LINE_RE = re.compile(r"^\s*(?P<name>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w]+)\s+(?P<type>[A-Za-z][\w\s()]+)")
_COLUMN_CONSTRAINT_RE = re.compile(
    r"\s+(PRIMARY\s+KEY|NOT\s+NULL|NULL|DEFAULT\b|REFERENCES\b|UNIQUE\b|CHECK\b|GENERATED\b|COLLATE\b).*",
    re.IGNORECASE | re.DOTALL,
)
_PRIMARY_KEY_COLUMNS_RE = re.compile(
    r"\bPRIMARY\s+KEY\b\s*(?:USING\s+\w+\s*)?\((?P<columns>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_IDENTIFIER_PREFIX_RE = re.compile(r"^\s*(?P<identifier>\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w.]+)")
_TABLE_KEYS = ("tables", "schema", "databaseSchema", "nativeSchema")


def _clean_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip('"`[] ')


def _split_sql_columns(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return parts


def _primary_key_columns_from_constraint(definition: str) -> list[str]:
    match = _PRIMARY_KEY_COLUMNS_RE.search(definition)
    if not match:
        return []

    columns = []
    for value in _split_sql_columns(match.group("columns")):
        identifier_match = _IDENTIFIER_PREFIX_RE.match(value)
        if not identifier_match:
            continue
        name = _clean_identifier(identifier_match.group("identifier"))
        if name:
            columns.append(name)
    return columns


def _normalize_column(column: Any) -> dict[str, Any] | None:
    if isinstance(column, str):
        name = _clean_identifier(column)
        return {"name": name} if name else None
    if not isinstance(column, dict):
        return None

    raw_name = column.get("name") or column.get("column") or column.get("columnName")
    name = _clean_identifier(raw_name)
    if not name:
        return None

    normalized = {"name": name}
    for source_key, target_key in (
        ("type", "type"),
        ("dataType", "type"),
        ("nullable", "nullable"),
        ("required", "mandatory"),
        ("primaryKey", "primaryKey"),
        ("foreignKey", "foreignKey"),
        ("default", "default"),
    ):
        if source_key in column and column[source_key] is not None:
            normalized[target_key] = column[source_key]
    return normalized


def _normalize_table(table: Any, source_ref: dict[str, str] | None = None) -> dict[str, Any] | None:
    if isinstance(table, str):
        name = _clean_identifier(table)
        if not name:
            return None
        normalized: dict[str, Any] = {"table": name, "columns": []}
    elif isinstance(table, dict):
        raw_name = table.get("table") or table.get("name") or table.get("tableName")
        name = _clean_identifier(raw_name)
        if not name:
            return None
        raw_columns = table.get("columns") or table.get("attributes") or table.get("fields") or []
        columns = [col for raw in raw_columns if (col := _normalize_column(raw))]
        normalized = {"table": name, "columns": columns}
        for key in ("primaryKey", "foreignKeys", "description"):
            if key in table and table[key] is not None:
                normalized[key] = table[key]
    else:
        return None

    if source_ref:
        normalized["relevantDocumentations"] = [{"docId": source_ref["doc_id"], "chunkId": source_ref["chunk_id"]}]
    return normalized


def _table_from_create_statement(match: re.Match[str], source_ref: dict[str, str] | None) -> dict[str, Any] | None:
    table_name = _clean_identifier(match.group("name"))
    columns: list[dict[str, Any]] = []
    primary_key: list[str] = []
    foreign_keys: list[dict[str, Any]] = []

    for definition in _split_sql_columns(match.group("body")):
        upper = definition.upper()
        if upper.startswith(("CONSTRAINT ", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE ", "CHECK ", "KEY ", "INDEX ")):
            if "PRIMARY KEY" in upper:
                primary_key.extend(_primary_key_columns_from_constraint(definition))
            if "FOREIGN KEY" in upper:
                foreign_keys.append({"definition": " ".join(definition.split())})
            continue

        column_match = _COLUMN_LINE_RE.match(definition)
        if not column_match:
            continue
        column_name = _clean_identifier(column_match.group("name"))
        column = {
            "name": column_name,
            "type": " ".join(_COLUMN_CONSTRAINT_RE.sub("", column_match.group("type")).split()),
            "nullable": "NOT NULL" not in upper,
            "primaryKey": "PRIMARY KEY" in upper,
            "generated": "GENERATED" in upper,
        }
        if column["primaryKey"]:
            primary_key.append(column_name)
        columns.append(column)

    normalized_primary_key = list(OrderedDict.fromkeys(primary_key))
    primary_key_lookup = {name.lower() for name in normalized_primary_key}
    for column in columns:
        if str(column.get("name") or "").lower() in primary_key_lookup:
            column["primaryKey"] = True

    return _normalize_table(
        {
            "table": table_name,
            "columns": columns,
            "primaryKey": normalized_primary_key,
            "foreignKeys": foreign_keys,
        },
        source_ref,
    )


def _extract_tables_from_mapping(value: Any, source_ref: dict[str, str] | None) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [table for item in value if (table := _normalize_table(item, source_ref))]
    if isinstance(value, dict):
        for key in _TABLE_KEYS:
            if key in value:
                return _extract_tables_from_mapping(value[key], source_ref)
        return [table for item in value.values() if (table := _normalize_table(item, source_ref))]
    return []


def _extract_tables_from_text(text: str, source_ref: dict[str, str] | None) -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        tables = _extract_tables_from_mapping(parsed, source_ref)
        if tables:
            return tables

    return [
        table
        for match in _CREATE_TABLE_RE.finditer(stripped)
        if (table := _table_from_create_statement(match, source_ref))
    ]


def collect_sql_tables(doc_items: Iterable[dict]) -> list[dict[str, Any]]:
    doc_items_list = list(doc_items)
    tables_by_name: OrderedDict[str, dict[str, Any]] = OrderedDict()
    refs_by_chunk = {
        ref.chunk_id: ref.to_internal_dict() for ref in build_chunk_references_from_doc_items(doc_items_list)
    }

    for item in doc_items_list:
        chunk_id = str(item.get("chunkId") or "").strip()
        source_ref = refs_by_chunk.get(chunk_id)
        for table in _extract_tables_from_text(normalize_to_text(item.get("content", "")), source_ref):
            name = str(table.get("table") or "").strip()
            if not name:
                continue
            existing = tables_by_name.get(name.lower())
            if existing is None:
                tables_by_name[name.lower()] = table
                continue
            existing_columns = {str(column.get("name")).lower(): column for column in existing.get("columns", [])}
            for column in table.get("columns", []):
                column_name = str(column.get("name") or "").lower()
                if column_name and column_name not in existing_columns:
                    existing.setdefault("columns", []).append(column)
            existing.setdefault("relevantDocumentations", []).extend(table.get("relevantDocumentations", []))

    return list(tables_by_name.values())


def object_class_name_from_table(table_name: str) -> str:
    base = table_name.strip().split(".")[-1]
    if base.endswith("ies") and len(base) > 3:
        base = f"{base[:-3]}y"
    elif base.endswith("s") and not base.endswith("ss") and len(base) > 3:
        base = base[:-1]
    parts = re.split(r"[_\-\s]+", base)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or table_name


def sql_type_to_attribute_type(sql_type: Any) -> tuple[str | None, str | None]:
    value = str(sql_type or "").lower()
    if any(token in value for token in ("char", "text", "uuid", "json", "xml")):
        return "string", "json" if "json" in value else None
    if any(token in value for token in ("bigint", "smallint", "integer", "int", "serial")):
        return "integer", "int64" if "big" in value else None
    if any(token in value for token in ("numeric", "decimal", "double", "float", "real")):
        return "number", None
    if any(token in value for token in ("bool", "bit")):
        return "boolean", None
    if any(token in value for token in ("timestamp", "datetime")):
        return "string", "date-time"
    if "date" in value:
        return "string", "date"
    return value or None, None


def tables_for_object_class(tables: list[dict[str, Any]], object_class: str) -> list[dict[str, Any]]:
    target = object_class.lower().strip()
    selected = [
        table
        for table in tables
        if object_class_name_from_table(str(table.get("table") or "")).lower() == target
        or str(table.get("table") or "").lower() == target
    ]
    if selected:
        return selected
    return [table for table in tables if target in str(table.get("table") or "").lower()]
