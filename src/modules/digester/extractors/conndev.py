# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Protocol-neutral reader for midPoint connector-development (conndev) exports.

A conndev upload is a JSON document exported from midPoint. Object-class documents share one
shadow-wrapped shape across protocols::

    {"<binding>": <shadow>, "uid": "...", "name": "...", "attributes": [<attribute shadow>, ...]}

Only the ``<binding>`` key differs and it is the protocol discriminator: ``scim`` carries the
SCIM schema URN, ``sql`` carries the database ``table``/``schema``. Everything else - the
``ri:conndev_Attribute`` shadows with their ConnId flags - is identical, so the attribute
flattening lives here and is shared by the SCIM baseline (``extractors/scim/baseline.py``) and
the SQL extractors (``extractors/sql/``). The document-shape primitives it builds on (shadow
unwrapping, binding detection) live in ``src.shared.content_types`` because upload-time ingest
needs them too.

midPoint also exports an embedded sub-class per complex attribute (``User__name``). Those
documents carry no class-level ``<binding>``; their protocol is declared only by their
attribute shadows, or not at all when the sub-class has no attributes.

The uploaded media type only says "this is a conndev export"; it does not say which protocol
it describes. Never infer the protocol from the content type - read the binding key.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.modules.digester.schemas.common import ChunkReference
from src.shared.content_types import (
    CONNDEV_SCIM_BINDING,
    CONNDEV_SQL_BINDING,
    conndev_attribute_shadows,
    detect_conndev_object_class_api_type,
    shadow_object_attributes,
)
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlObjectClassDocument:
    """One ``ri:conndev_sql`` object-class export: a database table and its ConnId attributes."""

    name: str
    table: str
    database_schema: str
    uid: str
    attributes: List[Dict[str, Any]] = field(default_factory=list)
    source_reference: Optional[ChunkReference] = None


def conndev_attribute_entries(value: Any) -> List[Dict[str, Any]]:
    """Return the dict-shaped attribute shadows of a conndev ``attributes`` field."""
    return [entry for entry in conndev_attribute_shadows(value) if isinstance(entry, dict)]


def flatten_shadow_connid_attribute(
    entry: Dict[str, Any],
    *,
    binding_key: str = CONNDEV_SCIM_BINDING,
    binding_path_field: str = "scimPath",
) -> Optional[Dict[str, Any]]:
    """
    Flatten one ``ri:conndev_Attribute`` shadow into the flat ConnId attribute shape.

    The ConnId flags (``type``, ``creatable``, ``updateable``, ``required``) live under
    ``connId``. When the attribute also carries a protocol binding with a wire path
    (``scim.path`` for SCIM), it is kept under ``binding_path_field`` and used as the
    attribute name of last resort.
    """
    attributes = shadow_object_attributes(entry)
    if attributes is None:
        return None

    connid_flags = attributes.get("connId")
    flattened: Dict[str, Any] = dict(connid_flags) if isinstance(connid_flags, dict) else {}

    binding = attributes.get(binding_key)
    binding_path = binding.get("path") if isinstance(binding, dict) else None
    if isinstance(binding_path, str) and binding_path.strip():
        flattened[binding_path_field] = binding_path.strip()

    name = attributes.get("name")
    if isinstance(name, str) and name.strip():
        flattened["name"] = name.strip()
    elif binding_path_field in flattened:
        flattened["name"] = flattened[binding_path_field]
    else:
        return None

    return flattened


def detect_object_class_binding(doc: Any) -> Optional[ApiType]:
    """
    Return the protocol a shadow-wrapped conndev object-class document describes.

    ``None`` means the document is not a shadow-wrapped object-class export (it may still be
    another conndev contract, e.g. a SCIM schema or ServiceProviderConfig document).
    """
    return detect_conndev_object_class_api_type(doc)


def parse_sql_object_class_document(
    doc: Dict[str, Any],
    *,
    source_reference: Optional[ChunkReference] = None,
) -> Optional[SqlObjectClassDocument]:
    """
    Parse one ``ri:conndev_sql`` object-class document into its table and ConnId attributes.

    Returns ``None`` when the document carries no usable table name, so callers can fall back
    to parsing a raw SQL schema instead of persisting a nameless object class.
    """
    sql_binding = shadow_object_attributes(doc.get(CONNDEV_SQL_BINDING)) or {}

    table = str(sql_binding.get("table") or "").strip()
    name = str(doc.get("name") or "").strip() or table
    if not table:
        logger.warning("[Digester:Conndev] Skipping SQL object class document: no physical table binding")
        return None

    attributes: List[Dict[str, Any]] = []
    for entry in conndev_attribute_entries(doc.get("attributes")):
        flattened = flatten_shadow_connid_attribute(
            entry,
            binding_key=CONNDEV_SQL_BINDING,
            binding_path_field="column",
        )
        if flattened is None:
            logger.warning(
                "[Digester:Conndev] Ignoring malformed attribute shadow in SQL object class document for table %s",
                table,
            )
            continue
        attributes.append(flattened)

    return SqlObjectClassDocument(
        name=name or table,
        table=table,
        database_schema=str(sql_binding.get("schema") or "").strip(),
        uid=str(doc.get("uid") or "").strip(),
        attributes=attributes,
        source_reference=source_reference,
    )
