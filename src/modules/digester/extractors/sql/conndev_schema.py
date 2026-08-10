# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Turn midPoint conndev SQL exports into the SQL table records the digester works with.

This is the authoritative source of SQL schema when the session was fed conndev object-class
exports (``ri:conndev_sql``): the export already names the table, its database schema and every
column with its ConnId type and flags, so nothing has to be guessed from prose or DDL.

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
from src.modules.digester.schemas.common import ChunkReference
from src.shared.content_types import is_conndev_documentation_item
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


CONNDEV_TABLE_SOURCE = "conndev"


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
        "table": definition.table,
        # The object-class name midPoint exported. It is authoritative and is used verbatim,
        # so a table exported as "m_user" stays "m_user" instead of being reshaped.
        "objectClass": definition.name,
        "columns": columns,
        "source": CONNDEV_TABLE_SOURCE,
    }
    if definition.database_schema:
        table["databaseSchema"] = definition.database_schema
    if definition.source_reference is not None:
        table["relevantDocumentations"] = [
            {"docId": definition.source_reference.doc_id, "chunkId": definition.source_reference.chunk_id}
        ]
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

    if detect_object_class_binding(parsed) is not ApiType.SQL:
        return []

    definition = parse_sql_object_class_document(parsed, source_reference=source_reference)
    return [_table_from_sql_object_class(definition)] if definition is not None else []


def is_conndev_table(table: Dict[str, Any]) -> bool:
    """Whether a table record came from a conndev export (authoritative column list)."""
    return table.get("source") == CONNDEV_TABLE_SOURCE
