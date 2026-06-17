# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any
from uuid import UUID

from src.common.jobs import update_job_progress
from src.modules.digester.extractors.sql.schema import collect_sql_tables, tables_for_object_class


async def extract_sql_tables(
    doc_items: list[dict],
    object_class: str,
    job_id: UUID,
) -> dict[str, Any]:
    """
    Select SQL tables for an object class.

    The public/session payload intentionally uses the existing `endpoints` key
    because SQL codegen already treats that operation-surface payload as table
    records.
    """
    await update_job_progress(
        job_id,
        total_processing=len(doc_items) or 1,
        processing_completed=0,
        message=f"Selecting SQL tables for {object_class}",
    )
    tables = tables_for_object_class(collect_sql_tables(doc_items), object_class)
    relevant_chunks: list[dict[str, Any]] = []
    seen_chunks: set[tuple[str, str]] = set()
    for table in tables:
        for chunk in table.get("relevantDocumentations", []):
            pair = (str(chunk.get("docId") or chunk.get("doc_id")), str(chunk.get("chunkId") or chunk.get("chunk_id")))
            if pair[0] and pair[1] and pair not in seen_chunks:
                relevant_chunks.append({"doc_id": pair[0], "chunk_id": pair[1]})
                seen_chunks.add(pair)

    await update_job_progress(
        job_id,
        processing_completed=len(doc_items) or 1,
        message=f"SQL table selection complete: {len(tables)} tables",
    )
    return {"result": {"endpoints": tables}, "relevantDocumentations": relevant_chunks}
