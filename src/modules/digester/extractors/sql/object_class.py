# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SQL object-class extraction.

Every table in the session's schema becomes an object-class candidate, whether it came from a
conndev SQL export (``ri:conndev_sql``) or from a raw database schema (``CREATE TABLE`` DDL or a
JSON table list). Nothing is filtered out here on a guess: an object class of a database
connector is always backed by a table, so there is nothing for an LLM to *discover* - only to
judge.

That judgement is the shared
:func:`~src.modules.digester.aggregation.object_class_ranking.deduplicate_and_sort_sql_object_classes`
step, which assigns the IGA/IDM confidence level and the final ordering. SQL therefore behaves
like SCIM: a deterministic contract in, the same ranking out.
"""

import logging
from typing import Any, Dict, List
from uuid import UUID

from src.documents.normalize import canonical_object_class_key
from src.jobs import update_job_progress
from src.modules.digester.aggregation.object_class_ranking import deduplicate_and_sort_sql_object_classes
from src.modules.digester.extractors.sql.conndev_schema import is_conndev_table
from src.modules.digester.extractors.sql.schema import (
    collect_sql_tables,
    object_class_name_for_table,
)
from src.modules.digester.schemas import ExtendedObjectClass
from src.modules.digester.selection import build_relevant_chunks_from_doc_items
from src.shared.coerce import as_list
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)

_DESCRIPTION_COLUMN_SAMPLE = 12
_RANKING_DESCRIPTION_COLUMN_SAMPLE = 4


def _describe_table(table: dict[str, Any], *, column_sample: int = _DESCRIPTION_COLUMN_SAMPLE) -> str:
    """Build a bounded table description for the final response or ranking context."""
    table_name = str(table.get("table") or "").strip()
    database_schema = str(table.get("databaseSchema") or "").strip()
    qualified = f"{database_schema}.{table_name}" if database_schema else table_name

    column_names = [name for column in as_list(table.get("columns")) if (name := str(column.get("name") or "").strip())]
    if not column_names:
        return f"Database table '{qualified}' with no documented columns."

    sample = ", ".join(column_names[:column_sample])
    remaining = len(column_names) - column_sample
    if remaining > 0:
        sample = f"{sample}, ... (+{remaining} more)"
    return f"Database table '{qualified}' with {len(column_names)} columns: {sample}."


def _object_class_from_table(table: dict[str, Any]) -> ExtendedObjectClass:
    return ExtendedObjectClass(
        name=object_class_name_for_table(table),
        description=_describe_table(table),
        superclass=None,
        abstract=False,
        embedded=False,
    )


def _chunk_refs_from_table(table: dict[str, Any]) -> List[Dict[str, str]]:
    """Convert a table's documentation references to the internal snake_case shape."""
    refs: List[Dict[str, str]] = []
    for chunk in as_list(table.get("relevantDocumentations")):
        if not isinstance(chunk, dict):
            continue
        doc_id = str(chunk.get("docId") or chunk.get("doc_id") or "").strip()
        chunk_id = str(chunk.get("chunkId") or chunk.get("chunk_id") or "").strip()
        if doc_id and chunk_id:
            refs.append({"doc_id": doc_id, "chunk_id": chunk_id})
    return refs


async def extract_sql_object_classes(doc_items: list[dict], job_id: UUID) -> dict[str, Any]:
    """
    Extract database connector object classes.

    Every table becomes a candidate; the shared ranking step decides which ones matter and in
    which order they are returned.
    """
    await update_job_progress(
        job_id,
        stage=JobStage.processing,
        total_processing=len(doc_items) or 1,
        processing_completed=0,
        message="Reading SQL schema",
    )

    tables = collect_sql_tables(doc_items)

    candidates: list[ExtendedObjectClass] = []
    class_to_chunks: Dict[str, List[Dict[str, str]]] = {}
    ranking_descriptions: Dict[str, str] = {}
    for table in tables:
        object_class = _object_class_from_table(table)
        candidates.append(object_class)
        class_key = canonical_object_class_key(object_class.name)
        ranking_descriptions[class_key] = _describe_table(
            table,
            column_sample=_RANKING_DESCRIPTION_COLUMN_SAMPLE,
        )
        chunk_refs = _chunk_refs_from_table(table)
        if chunk_refs:
            class_to_chunks.setdefault(class_key, []).extend(chunk_refs)

    logger.info(
        "[Digester:ObjectClasses] SQL schema read: %s tables (%s from a conndev export)",
        len(tables),
        sum(1 for table in tables if is_conndev_table(table)),
    )

    result = await deduplicate_and_sort_sql_object_classes(
        candidates,
        job_id,
        class_to_chunks=class_to_chunks,
        ranking_descriptions=ranking_descriptions,
    )

    relevant_chunks = build_relevant_chunks_from_doc_items(doc_items)
    logger.info("[Digester:ObjectClasses] Completed. Total classes: %d", len(result.objectClasses))

    return {
        "result": result.model_dump(by_alias=True, mode="json"),
        "relevantDocumentations": relevant_chunks,
    }
