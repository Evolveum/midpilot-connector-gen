# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Turn midPoint conndev SQL exports into the SQL table records the digester works with.

Conndev supplies two complementary documents: an object-class export (``ri:conndev_sql``)
describes logical ConnId attributes and flags, while a SQL-table export carries physical column
types and constraints. Merging both gives the digester a complete schema without guessing from
prose or DDL.

Raw ``CREATE TABLE`` / JSON schema parsing lives in ``schema.py`` and stays the fallback for
sessions that upload a database schema directly. Both produce the same table record shape, so
object-class, table and attribute extraction have a single downstream path.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.documents.chunking import normalize_to_text
from src.modules.digester.extractors.conndev import (
    SqlObjectClassDocument,
    detect_object_class_binding,
    parse_sql_object_class_document,
)
from src.modules.digester.extractors.sql.identifiers import clean_sql_identifier
from src.modules.digester.schemas.common import ChunkReference
from src.shared.coerce import as_nonempty_str
from src.shared.content_types import (
    CONNDEV_SQL_TABLE_CONTENT,
    is_conndev_documentation_item,
    is_conndev_sql_table_document,
)
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


CONNDEV_TABLE_SOURCE = "conndev"
CONNDEV_SQL_TABLE_SOURCE = "conndev_sql_table"


_CONNID_TYPE_MAP: Dict[str, tuple[str, Optional[str]]] = {
    "string": ("string", None),
    "character": ("string", None),
    "guardedstring": ("string", "password"),
    "boolean": ("boolean", None),
    "integer": ("integer", None),
    "int": ("integer", None),
    "long": ("integer", "int64"),
    "biginteger": ("integer", "int64"),
    "float": ("number", None),
    "double": ("number", None),
    "bigdecimal": ("number", None),
    "zoneddatetime": ("string", "date-time"),
    "offsetdatetime": ("string", "date-time"),
    "localdatetime": ("string", "date-time"),
    "instant": ("string", "date-time"),
    "localdate": ("string", "date"),
    "localtime": ("string", "time"),
    "binary": ("string", "binary"),
    "bytearray": ("string", "binary"),
    "map": ("object", None),
}


def connid_type_to_attribute_type(connid_type: Any) -> tuple[Optional[str], Optional[str]]:
    """Map a ConnId attribute type to the digester ``(type, format)`` pair."""
    normalized = str(connid_type or "").strip().lower()
    if not normalized:
        return None, None

    mapped = _CONNID_TYPE_MAP.get(normalized)
    if mapped is None:
        logger.warning("[Digester:Conndev] Unknown ConnId attribute type %r; defaulting to string", connid_type)
        return "string", None
    return mapped


def _column_from_connid_attribute(attribute: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Build one table column from a flattened ConnId attribute.

    Only flags the export actually carries are set; the rest is left unset so attribute
    extraction can apply its own defaults instead of inventing a value here.
    """
    name = str(attribute.get("name") or "").strip()
    if not name:
        return None

    column: Dict[str, Any] = {"name": name, "connIdType": attribute.get("type")}

    column_name = attribute.get("column")
    if isinstance(column_name, str) and column_name.strip():
        # ``name`` is the logical ConnId attribute exposed to midPoint. The SQL binding may
        # point it at a differently named physical column, so keep both identities.
        column["column"] = column_name.strip()

    if "required" in attribute:
        column["mandatory"] = bool(attribute.get("required"))
    if "creatable" in attribute:
        column["creatable"] = bool(attribute.get("creatable"))
    if "updateable" in attribute or "updatable" in attribute:
        column["updatable"] = bool(attribute.get("updatable", attribute.get("updateable")))

    return column


def _table_from_sql_object_class(definition: SqlObjectClassDocument) -> Dict[str, Any]:
    """Convert one parsed conndev SQL object class into a table record."""
    columns = [column for attribute in definition.attributes if (column := _column_from_connid_attribute(attribute))]

    table: Dict[str, Any] = {
        "table": clean_sql_identifier(definition.table),
        # The object-class name midPoint exported. It is authoritative and is used verbatim,
        # so a table exported as "m_user" stays "m_user" instead of being reshaped.
        "objectClass": definition.name,
        "columns": columns,
        "source": CONNDEV_TABLE_SOURCE,
    }
    database_schema = clean_sql_identifier(definition.database_schema)
    if database_schema:
        table["databaseSchema"] = database_schema
    if definition.source_reference is not None:
        table["relevantDocumentations"] = [
            {"docId": definition.source_reference.doc_id, "chunkId": definition.source_reference.chunk_id}
        ]
    return table


def _foreign_key_from_sql_table_column(column: Dict[str, Any], column_name: str) -> Dict[str, str] | None:
    constraint_name = as_nonempty_str(column.get("foreignKeyName"))
    referenced_table = as_nonempty_str(column.get("referencedTable"))
    referenced_column = as_nonempty_str(column.get("referencedColumn"))
    if constraint_name is None and referenced_table is None and referenced_column is None:
        return None
    if referenced_table is None or referenced_column is None:
        logger.warning(
            "[Digester:Conndev] Ignoring incomplete foreign key metadata for column %r",
            column_name,
        )
        return None
    foreign_key = {
        "referencedTable": referenced_table,
        "referencedColumn": referenced_column,
    }
    if constraint_name is not None:
        foreign_key["constraintName"] = constraint_name
    return foreign_key


def _column_from_sql_table(column: Any) -> Dict[str, Any] | None:
    if not isinstance(column, dict):
        logger.warning("[Digester:Conndev] Ignoring a SQL-table column that is not an object")
        return None

    name = as_nonempty_str(column.get("name"))
    if name is None:
        logger.warning("[Digester:Conndev] Ignoring a SQL-table column without a name")
        return None

    normalized: Dict[str, Any] = {"name": name}
    type_name = as_nonempty_str(column.get("typeName"))
    if type_name is not None:
        normalized["type"] = type_name

    for key in ("nullable", "primaryKey"):
        value = column.get(key)
        if isinstance(value, bool):
            normalized[key] = value
        elif value is not None:
            logger.warning(
                "[Digester:Conndev] Ignoring non-boolean %s metadata for column %r",
                key,
                name,
            )

    if column.get("defaultValue") is not None:
        normalized["default"] = column["defaultValue"]
    if column.get("autoIncrement") is True:
        normalized["generated"] = True

    foreign_key = _foreign_key_from_sql_table_column(column, name)
    if foreign_key is not None:
        normalized["foreignKey"] = foreign_key
    return normalized


def _foreign_keys_from_columns(columns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    invalid_constraints: set[str] = set()
    for column in columns:
        foreign_key = column.get("foreignKey")
        if not isinstance(foreign_key, dict):
            continue
        constraint_name = as_nonempty_str(foreign_key.get("constraintName"))
        if constraint_name is None:
            continue
        referenced_table = str(foreign_key["referencedTable"])
        existing = grouped.get(constraint_name)
        if existing is None:
            grouped[constraint_name] = {
                "constraintName": constraint_name,
                "columns": [column["name"]],
                "referencedTable": referenced_table,
                "referencedColumns": [foreign_key["referencedColumn"]],
            }
            continue
        if existing["referencedTable"] != referenced_table:
            logger.warning(
                "[Digester:Conndev] Ignoring inconsistent table-level foreign key %r",
                constraint_name,
            )
            invalid_constraints.add(constraint_name)
            continue
        existing["columns"].append(column["name"])
        existing["referencedColumns"].append(foreign_key["referencedColumn"])
    return [value for key, value in grouped.items() if key not in invalid_constraints]


def _identity_values_match(identity_field: str, outer_value: str, inner_value: str) -> bool:
    """Compare duplicated wrapper/tableContent identity without treating SQL syntax as identity."""
    if identity_field == "tableType":
        return outer_value.casefold() == inner_value.casefold()
    return clean_sql_identifier(outer_value).casefold() == clean_sql_identifier(inner_value).casefold()


def _table_from_sql_table_document(
    document: Dict[str, Any], source_reference: Optional[ChunkReference]
) -> Dict[str, Any] | None:
    try:
        table_content = json.loads(document[CONNDEV_SQL_TABLE_CONTENT])
    except (KeyError, TypeError, json.JSONDecodeError):
        logger.warning("[Digester:Conndev] Ignoring SQL-table export with invalid tableContent JSON")
        return None
    if not isinstance(table_content, dict):
        logger.warning("[Digester:Conndev] Ignoring SQL-table export whose tableContent is not an object")
        return None

    for identity_field in ("catalog", "schema", "name", "tableType"):
        outer_value = as_nonempty_str(document.get(identity_field))
        inner_value = as_nonempty_str(table_content.get(identity_field))
        if (
            outer_value is not None
            and inner_value is not None
            and not _identity_values_match(identity_field, outer_value, inner_value)
        ):
            logger.warning(
                "[Digester:Conndev] Ignoring SQL-table export with mismatched %s metadata",
                identity_field,
            )
            return None

    table_name = clean_sql_identifier(table_content.get("name") or document.get("name"))
    raw_columns = table_content.get("columns")
    if not table_name or not isinstance(raw_columns, list):
        logger.warning("[Digester:Conndev] Ignoring SQL-table export without a valid name and columns list")
        return None

    columns = [column for raw_column in raw_columns if (column := _column_from_sql_table(raw_column))]
    table: Dict[str, Any] = {
        "table": table_name,
        "columns": columns,
        "source": CONNDEV_SQL_TABLE_SOURCE,
        "foreignKeys": _foreign_keys_from_columns(columns),
    }
    if (
        raw_columns
        and len(columns) == len(raw_columns)
        and all(
            isinstance(raw_column, dict) and isinstance(raw_column.get("primaryKey"), bool)
            for raw_column in raw_columns
        )
    ):
        table["primaryKey"] = [column["name"] for column in columns if column.get("primaryKey") is True]

    database_schema = clean_sql_identifier(table_content.get("schema") or document.get("schema"))
    if database_schema:
        table["databaseSchema"] = database_schema
    if source_reference is not None:
        table["relevantDocumentations"] = [{"docId": source_reference.doc_id, "chunkId": source_reference.chunk_id}]
    return table


def extract_conndev_sql_tables(
    item: Dict[str, Any], source_reference: Optional[ChunkReference]
) -> List[Dict[str, Any]]:
    """
    Read one documentation item as a conndev SQL object-class export.

    Returns an empty list when the item is not such an export, so the caller can fall back to
    raw SQL schema parsing.
    """
    if not is_conndev_documentation_item(item):
        return []

    content = normalize_to_text(item.get("content", "")).strip()
    if not content:
        return []

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []

    if is_conndev_sql_table_document(parsed):
        table = _table_from_sql_table_document(parsed, source_reference)
        return [table] if table is not None else []

    if detect_object_class_binding(parsed) is not ApiType.SQL:
        return []

    definition = parse_sql_object_class_document(parsed, source_reference=source_reference)
    return [_table_from_sql_object_class(definition)] if definition is not None else []


def is_conndev_table(table: Dict[str, Any]) -> bool:
    """Whether a table record came from a conndev export (authoritative column list)."""
    return table.get("source") == CONNDEV_TABLE_SOURCE
