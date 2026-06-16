# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any
from uuid import UUID

from src.common.jobs import update_job_progress
from src.modules.digester.extractors.sql.schema import (
    collect_sql_tables,
    sql_type_to_attribute_type,
    tables_for_object_class,
)

logger = logging.getLogger(__name__)


def _attribute_from_column(column: dict[str, Any], table: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    name = str(column.get("name") or "").strip()
    if not name:
        return None

    attr_type, attr_format = sql_type_to_attribute_type(column.get("type"))
    mandatory = column.get("mandatory")
    if mandatory is None and column.get("nullable") is not None:
        mandatory = not bool(column.get("nullable"))

    relevant_documentations = table.get("relevantDocumentations")
    if not isinstance(relevant_documentations, list):
        relevant_documentations = []

    return name, {
        "type": attr_type,
        "format": attr_format,
        "description": f"Column '{name}' from table '{table.get('table')}'.",
        "mandatory": mandatory,
        "updatable": not bool(column.get("primaryKey")),
        "creatable": not bool(column.get("generated")),
        "readable": True,
        "multivalue": False,
        "returnedByDefault": True,
        "table": table.get("table"),
        "column": name,
        "primaryKey": column.get("primaryKey"),
        "relevantDocumentations": relevant_documentations,
    }


async def extract_sql_attributes(
    doc_items: list[dict],
    object_class: str,
    job_id: UUID,
) -> dict[str, Any]:
    """Build SQL attributes deterministically from selected schema tables."""
    await update_job_progress(
        job_id,
        total_processing=len(doc_items) or 1,
        processing_completed=0,
        message=f"Extracting SQL columns for {object_class}",
    )

    tables = tables_for_object_class(collect_sql_tables(doc_items), object_class)
    attributes: dict[str, dict[str, Any]] = {}
    relevant_chunks: list[dict[str, Any]] = []
    seen_chunks: set[tuple[str, str]] = set()

    for table in tables:
        for chunk in table.get("relevantDocumentations", []):
            pair = (str(chunk.get("docId") or chunk.get("doc_id")), str(chunk.get("chunkId") or chunk.get("chunk_id")))
            if pair[0] and pair[1] and pair not in seen_chunks:
                relevant_chunks.append({"doc_id": pair[0], "chunk_id": pair[1]})
                seen_chunks.add(pair)
        for column in table.get("columns", []):
            if not isinstance(column, dict):
                continue
            attribute = _attribute_from_column(column, table)
            if attribute is None:
                continue
            name, payload = attribute
            attributes.setdefault(name, payload)

    await update_job_progress(
        job_id,
        processing_completed=len(doc_items) or 1,
        message=f"SQL attribute extraction complete: {len(attributes)} attributes",
    )
    return {"result": {"attributes": attributes}, "relevantDocumentations": relevant_chunks}
