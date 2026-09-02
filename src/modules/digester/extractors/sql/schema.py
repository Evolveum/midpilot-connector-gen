# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from src.documents.chunking import normalize_to_text
from src.modules.digester.errors import SqlTableIdentityConflictError
from src.modules.digester.extractors.sql.conndev_schema import extract_conndev_sql_tables, is_conndev_table
from src.modules.digester.extractors.sql.identifiers import (
    clean_sql_identifier,
    clean_sql_identifier_component,
    split_sql_table_identifier,
)
from src.modules.digester.schemas.common import ChunkReference, build_chunk_references_from_doc_items

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

_DATABASE_COLUMN_FIELDS = frozenset({"type", "nullable", "primaryKey", "foreignKey", "default", "generated"})
_DATABASE_TABLE_FIELDS = frozenset({"primaryKey", "foreignKeys", "description"})
_CONNDEV_TABLE_FIELDS = frozenset({"objectClass", "databaseSchema", "source"})


@dataclass(frozen=True)
class _SqlTableIdentity:
    """Normalized physical identity used only while reconciling extracted SQL records."""

    catalog: str
    schema: str
    table: str

    def is_compatible_with(self, other: "_SqlTableIdentity") -> bool:
        return (
            self.table == other.table
            and (not self.schema or not other.schema or self.schema == other.schema)
            and (not self.catalog or not other.catalog or self.catalog == other.catalog)
        )

    def render(self) -> str:
        return ".".join(component for component in (self.catalog, self.schema, self.table) if component)


@dataclass
class _CollectedTable:
    table: dict[str, Any]
    position: int


def _normalized_identity_component(value: Any) -> str:
    return clean_sql_identifier_component(value).casefold()


def _table_identity(table: dict[str, Any]) -> _SqlTableIdentity:
    return _SqlTableIdentity(
        catalog=_normalized_identity_component(table.get("databaseCatalog")),
        schema=_normalized_identity_component(table.get("databaseSchema")),
        table=clean_sql_identifier(table.get("table")).casefold(),
    )


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
        name = clean_sql_identifier(identifier_match.group("identifier"))
        if name:
            columns.append(name)
    return columns


def _normalize_column(column: Any) -> dict[str, Any] | None:
    if isinstance(column, str):
        name = clean_sql_identifier(column)
        return {"name": name} if name else None
    if not isinstance(column, dict):
        return None

    raw_name = column.get("name") or column.get("column") or column.get("columnName")
    name = clean_sql_identifier(raw_name)
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
        ("generated", "generated"),
    ):
        if source_key in column and column[source_key] is not None:
            normalized[target_key] = column[source_key]
    return normalized


def _normalize_table(table: Any, source_ref: dict[str, str] | None = None) -> dict[str, Any] | None:
    if isinstance(table, str):
        qualified_catalog, qualified_schema, name = split_sql_table_identifier(table)
        if not name:
            return None
        normalized: dict[str, Any] = {"table": name, "columns": []}
        if qualified_catalog:
            normalized["databaseCatalog"] = qualified_catalog
        if qualified_schema:
            normalized["databaseSchema"] = qualified_schema
    elif isinstance(table, dict):
        raw_name = table.get("table") or table.get("name") or table.get("tableName")
        qualified_catalog, qualified_schema, name = split_sql_table_identifier(raw_name)
        if not name:
            return None
        raw_columns = table.get("columns") or table.get("attributes") or table.get("fields") or []
        columns = [col for raw in raw_columns if (col := _normalize_column(raw))]
        normalized = {"table": name, "columns": columns}
        database_catalog = clean_sql_identifier_component(table.get("databaseCatalog") or table.get("catalog"))
        if database_catalog or qualified_catalog:
            normalized["databaseCatalog"] = database_catalog or qualified_catalog
        database_schema = clean_sql_identifier_component(table.get("databaseSchema") or table.get("schema"))
        if database_schema or qualified_schema:
            normalized["databaseSchema"] = database_schema or qualified_schema
        for key in ("primaryKey", "foreignKeys", "description"):
            if key in table and table[key] is not None:
                normalized[key] = table[key]

        table_primary_key = normalized.get("primaryKey")
        if isinstance(table_primary_key, list):
            primary_key_columns = {
                cleaned.casefold() for value in table_primary_key if (cleaned := clean_sql_identifier(value))
            }
            for column in columns:
                column_name = clean_sql_identifier(column.get("column") or column.get("name"))
                if column_name:
                    column["primaryKey"] = column_name.casefold() in primary_key_columns
    else:
        return None

    if source_ref:
        normalized["relevantDocumentations"] = [{"docId": source_ref["doc_id"], "chunkId": source_ref["chunk_id"]}]
    return normalized


def _table_from_create_statement(match: re.Match[str], source_ref: dict[str, str] | None) -> dict[str, Any] | None:
    table_name = match.group("name")
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
        column_name = clean_sql_identifier(column_match.group("name"))
        column = {
            "name": column_name,
            "type": " ".join(_COLUMN_CONSTRAINT_RE.sub("", column_match.group("type")).split()),
            "nullable": "NOT NULL" not in upper,
            "primaryKey": "PRIMARY KEY" in upper,
        }
        if "GENERATED" in upper:
            column["generated"] = True
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
        return [
            table for item in value.values() if isinstance(item, dict) and (table := _normalize_table(item, source_ref))
        ]
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


def _merge_relevant_documentations(existing: dict[str, Any], incoming: list[Any]) -> None:
    """Append documentation references the table does not already carry."""
    merged = existing.setdefault("relevantDocumentations", [])
    seen = {(str(ref.get("docId")), str(ref.get("chunkId"))) for ref in merged if isinstance(ref, dict)}
    for ref in incoming:
        if not isinstance(ref, dict):
            continue
        pair = (str(ref.get("docId")), str(ref.get("chunkId")))
        if pair not in seen:
            merged.append(ref)
            seen.add(pair)


def _column_identity(column: dict[str, Any]) -> str:
    """Return the physical database-column identity used to join Conndev and DDL records."""
    return clean_sql_identifier(column.get("column") or column.get("name")).lower()


def _merge_cross_source_column(conndev_column: dict[str, Any], database_column: dict[str, Any]) -> dict[str, Any]:
    """Combine one logical ConnId attribute with its physical database declaration."""
    merged = dict(conndev_column)
    for field in _DATABASE_COLUMN_FIELDS:
        if field in database_column:
            merged[field] = database_column[field]
    return merged


def _merge_columns(
    existing: dict[str, Any], incoming: dict[str, Any], *, existing_is_conndev: bool, incoming_is_conndev: bool
) -> list[dict[str, Any]]:
    existing_columns = [column for column in existing.get("columns", []) if isinstance(column, dict)]
    incoming_columns = [column for column in incoming.get("columns", []) if isinstance(column, dict)]

    if existing_is_conndev != incoming_is_conndev:
        conndev_columns = existing_columns if existing_is_conndev else incoming_columns
        database_columns = incoming_columns if existing_is_conndev else existing_columns
        database_by_name = {_column_identity(column): column for column in database_columns}
        merged_columns: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Conndev order and logical names are stable; database-only columns follow afterwards.
        for conndev_column in conndev_columns:
            identity = _column_identity(conndev_column)
            database_column = database_by_name.get(identity)
            merged_columns.append(
                _merge_cross_source_column(conndev_column, database_column)
                if identity and database_column is not None
                else conndev_column
            )
            if identity:
                seen.add(identity)
        merged_columns.extend(
            column for column in database_columns if not (identity := _column_identity(column)) or identity not in seen
        )
        return merged_columns

    # Preserve the established first-document precedence for duplicate records from the same
    # source, while still adding columns that only the later document declares.
    merged_columns = list(existing_columns)
    seen = {_column_identity(column) for column in existing_columns}
    merged_columns.extend(
        column for column in incoming_columns if not (identity := _column_identity(column)) or identity not in seen
    )
    return merged_columns


def _merge_table_metadata(
    existing: dict[str, Any], incoming: dict[str, Any], *, existing_is_conndev: bool, incoming_is_conndev: bool
) -> None:
    """Merge table-level metadata with protocol-specific source precedence."""
    if existing_is_conndev != incoming_is_conndev:
        conndev_table = existing if existing_is_conndev else incoming
        database_table = incoming if existing_is_conndev else existing
        for field in _CONNDEV_TABLE_FIELDS:
            if field in conndev_table:
                existing[field] = conndev_table[field]
        for field in _DATABASE_TABLE_FIELDS:
            if field in database_table:
                existing[field] = database_table[field]
        if "databaseCatalog" in database_table:
            existing["databaseCatalog"] = database_table["databaseCatalog"]
        if "databaseSchema" not in existing and "databaseSchema" in database_table:
            existing["databaseSchema"] = database_table["databaseSchema"]
        return

    for key, value in incoming.items():
        if key not in {"columns", "relevantDocumentations"} and key not in existing:
            existing[key] = value


def _extract_tables_from_item(item: dict, source_ref: dict[str, str] | None) -> list[dict[str, Any]]:
    """
    Read one documentation item as SQL tables.

    A conndev SQL export is authoritative and wins; anything else is parsed as a raw SQL schema
    (JSON table list or ``CREATE TABLE`` DDL).
    """
    conndev_tables = extract_conndev_sql_tables(item, ChunkReference(**source_ref) if source_ref else None)
    if conndev_tables:
        return conndev_tables
    return _extract_tables_from_text(normalize_to_text(item.get("content", "")), source_ref)


def _same_source_key(table: dict[str, Any]) -> tuple[bool, _SqlTableIdentity, str]:
    """Keep distinct logical classes separate until their physical bindings are validated."""
    logical_name = str(table.get("objectClass") or "").strip().casefold() if is_conndev_table(table) else ""
    return is_conndev_table(table), _table_identity(table), logical_name


def _coalesce_same_source_tables(tables: list[_CollectedTable]) -> list[_CollectedTable]:
    """Apply established first-document precedence only to records with the same full identity."""
    by_identity: OrderedDict[tuple[bool, _SqlTableIdentity, str], _CollectedTable] = OrderedDict()
    for record in tables:
        key = _same_source_key(record.table)
        existing_record = by_identity.get(key)
        if existing_record is None:
            by_identity[key] = record
            continue

        existing = existing_record.table
        incoming = record.table
        existing_is_conndev = is_conndev_table(existing)
        incoming_is_conndev = is_conndev_table(incoming)
        existing["columns"] = _merge_columns(
            existing,
            incoming,
            existing_is_conndev=existing_is_conndev,
            incoming_is_conndev=incoming_is_conndev,
        )
        _merge_table_metadata(
            existing,
            incoming,
            existing_is_conndev=existing_is_conndev,
            incoming_is_conndev=incoming_is_conndev,
        )
        _merge_relevant_documentations(existing, incoming.get("relevantDocumentations", []))
    return list(by_identity.values())


def _identity_conflict(table_name: str, records: list[_CollectedTable]) -> SqlTableIdentityConflictError:
    return SqlTableIdentityConflictError(
        table_name,
        [_table_identity(record.table).render() for record in records],
    )


def _merge_complementary_tables(logical: _CollectedTable, database: _CollectedTable) -> _CollectedTable:
    """Build a logical table enriched only by the database fields the digester understands."""
    merged = dict(logical.table)
    merged["columns"] = _merge_columns(
        logical.table,
        database.table,
        existing_is_conndev=True,
        incoming_is_conndev=False,
    )
    _merge_table_metadata(
        merged,
        database.table,
        existing_is_conndev=True,
        incoming_is_conndev=False,
    )

    merged.pop("relevantDocumentations", None)
    for record in sorted((logical, database), key=lambda item: item.position):
        _merge_relevant_documentations(merged, record.table.get("relevantDocumentations", []))
    return _CollectedTable(merged, min(logical.position, database.position))


def _reconcile_complementary_sources(records: list[_CollectedTable]) -> list[_CollectedTable]:
    """Pair logical and physical records one-to-one without treating missing identity as a guess."""
    by_table_name: OrderedDict[str, list[int]] = OrderedDict()
    for index, record in enumerate(records):
        by_table_name.setdefault(_table_identity(record.table).table, []).append(index)

    logical_pairs: dict[int, int] = {}
    paired_database: set[int] = set()
    for table_name, indices in by_table_name.items():
        logical_indices = [index for index in indices if is_conndev_table(records[index].table)]
        database_indices = [index for index in indices if not is_conndev_table(records[index].table)]
        if not logical_indices or not database_indices:
            continue

        matches_by_logical = {
            logical_index: [
                database_index
                for database_index in database_indices
                if _table_identity(records[logical_index].table).is_compatible_with(
                    _table_identity(records[database_index].table)
                )
            ]
            for logical_index in logical_indices
        }
        matches_by_database = {
            database_index: [
                logical_index
                for logical_index in logical_indices
                if database_index in matches_by_logical[logical_index]
            ]
            for database_index in database_indices
        }
        if any(len(matches) > 1 for matches in (*matches_by_logical.values(), *matches_by_database.values())):
            raise _identity_conflict(table_name, [records[index] for index in indices])

        unmatched_logical = [index for index, matches in matches_by_logical.items() if not matches]
        unmatched_database = [index for index, matches in matches_by_database.items() if not matches]
        if unmatched_logical and unmatched_database:
            raise _identity_conflict(
                table_name,
                [records[index] for index in (*unmatched_logical, *unmatched_database)],
            )

        for logical_index, matches in matches_by_logical.items():
            if matches:
                logical_pairs[logical_index] = matches[0]
                paired_database.add(matches[0])

    reconciled: list[_CollectedTable] = []
    for index, record in enumerate(records):
        database_index = logical_pairs.get(index)
        if database_index is not None:
            reconciled.append(_merge_complementary_tables(record, records[database_index]))
        elif index not in paired_database:
            reconciled.append(record)
    reconciled.sort(key=lambda item: item.position)
    return reconciled


def _qualify_colliding_object_classes(records: list[_CollectedTable]) -> None:
    """Give colliding public class names stable physical identities the GUI can round-trip."""
    by_object_class: OrderedDict[str, list[_CollectedTable]] = OrderedDict()
    for record in records:
        object_class = object_class_name_for_table(record.table).strip()
        by_object_class.setdefault(object_class.casefold(), []).append(record)

    for colliding in by_object_class.values():
        identities = {_table_identity(record.table) for record in colliding}
        if len(identities) < 2:
            continue
        if any(left != right and left.is_compatible_with(right) for left in identities for right in identities):
            raise _identity_conflict(object_class_name_for_table(colliding[0].table), colliding)
        for record in colliding:
            record.table["objectClass"] = _table_identity(record.table).render()

    qualified_names: dict[str, _CollectedTable] = {}
    for record in records:
        object_class = object_class_name_for_table(record.table).strip()
        existing = qualified_names.get(object_class.casefold())
        if existing is not None and _table_identity(existing.table) != _table_identity(record.table):
            raise _identity_conflict(object_class, [existing, record])
        qualified_names[object_class.casefold()] = record


def collect_sql_tables(doc_items: Iterable[dict]) -> list[dict[str, Any]]:
    doc_items_list = list(doc_items)
    refs_by_chunk = {
        ref.chunk_id: ref.to_internal_dict() for ref in build_chunk_references_from_doc_items(doc_items_list)
    }

    extracted: list[_CollectedTable] = []
    position = 0
    for item in doc_items_list:
        chunk_id = str(item.get("chunkId") or "").strip()
        source_ref = refs_by_chunk.get(chunk_id)
        for table in _extract_tables_from_item(item, source_ref):
            name = clean_sql_identifier(table.get("table"))
            if not name:
                continue
            table["table"] = name
            extracted.append(_CollectedTable(table, position))
            position += 1

    reconciled = _reconcile_complementary_sources(_coalesce_same_source_tables(extracted))
    _qualify_colliding_object_classes(reconciled)
    return [record.table for record in reconciled]


# Word endings that look plural but are not, so a trailing "s" must be kept
# (``m_focus`` -> ``MFocus``, ``m_status`` -> ``MStatus``, ``m_address`` -> ``MAddress``).
_NON_PLURAL_SUFFIXES: tuple[str, ...] = ("ss", "us", "is")


def object_class_name_for_table(table: dict[str, Any]) -> str:
    """
    Resolve the object-class name of a table record.

    A conndev export states the name midPoint uses and it is taken verbatim; a raw database
    schema has no such name, so it is derived from the table name.
    """
    exported_name = str(table.get("objectClass") or "").strip()
    return exported_name or object_class_name_from_table(str(table.get("table") or ""))


def object_class_name_from_table(table_name: str) -> str:
    base = table_name.strip().split(".")[-1]
    if base.endswith("ies") and len(base) > 3:
        base = f"{base[:-3]}y"
    elif base.endswith("s") and not base.lower().endswith(_NON_PLURAL_SUFFIXES) and len(base) > 3:
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
    explicitly_bound = [table for table in tables if str(table.get("objectClass") or "").strip().lower() == target]
    if explicitly_bound:
        return explicitly_bound

    derived = [
        table for table in tables if object_class_name_from_table(str(table.get("table") or "")).lower() == target
    ]
    if derived:
        return derived

    physical_name = [table for table in tables if str(table.get("table") or "").lower() == target]
    if physical_name:
        return physical_name
    return [table for table in tables if target in str(table.get("table") or "").lower()]
