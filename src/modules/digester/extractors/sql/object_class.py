# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any
from uuid import UUID

from src.common.jobs import update_job_progress
from src.common.llm import build_structured_chain
from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.extractors.sql.schema import collect_sql_tables, object_class_name_from_table
from src.modules.digester.prompts.sql.object_class_prompts import (
    sql_object_class_system_prompt,
    sql_object_class_user_prompt,
)
from src.modules.digester.schemas import ExtendedObjectClass, FinalObjectClass, ObjectClassesExtendedResponse
from src.modules.digester.utils.doc_chunk import build_relevant_chunks_from_doc_items
from src.modules.digester.utils.object_classes import confidence_order_key

logger = logging.getLogger(__name__)

_TECHNICAL_TABLE_MARKERS = (
    "audit",
    "cache",
    "flyway",
    "history",
    "liquibase",
    "log",
    "migration",
    "schema_version",
    "tmp",
)
_IGA_TABLE_MARKERS = (
    "account",
    "assignment",
    "employee",
    "entitlement",
    "group",
    "membership",
    "organization",
    "permission",
    "person",
    "role",
    "user",
)


def _is_probably_domain_table(table: dict[str, Any]) -> bool:
    table_name = str(table.get("table") or "").lower()
    if not table_name or any(marker in table_name for marker in _TECHNICAL_TABLE_MARKERS):
        return False
    if any(marker in table_name for marker in _IGA_TABLE_MARKERS):
        return True

    columns = {str(column.get("name") or "").lower() for column in table.get("columns", [])}
    identity_columns = {"id", "uid", "username", "user_name", "login", "email", "name", "display_name"}
    return bool(columns.intersection(identity_columns)) and len(columns) >= 3


def _object_class_from_table(table: dict[str, Any]) -> FinalObjectClass:
    table_name = str(table.get("table") or "").strip()
    raw_columns = table.get("columns")
    columns = raw_columns if isinstance(raw_columns, list) else []
    raw_relevant_documentations = table.get("relevantDocumentations")
    relevant_documentations = raw_relevant_documentations if isinstance(raw_relevant_documentations, list) else []
    return FinalObjectClass(
        name=object_class_name_from_table(table_name),
        relevant=RelevantLevel.TRUE,
        confidence=ConfidenceLevel.HIGH
        if any(marker in table_name.lower() for marker in _IGA_TABLE_MARKERS)
        else ConfidenceLevel.MEDIUM,
        superclass=None,
        abstract=False,
        embedded=False,
        description=f"Database object mapped from table '{table_name}' with {len(columns)} columns.",
        relevantDocumentations=relevant_documentations,
    )


def _merge_sql_object_classes(object_classes: list[FinalObjectClass]) -> list[FinalObjectClass]:
    by_name: dict[str, FinalObjectClass] = {}
    for obj_class in object_classes:
        key = obj_class.name.strip().lower()
        if not key:
            continue
        existing = by_name.get(key)
        if existing is None:
            by_name[key] = obj_class
            continue
        if confidence_order_key(obj_class.confidence) < confidence_order_key(existing.confidence):
            existing.confidence = obj_class.confidence
        if obj_class.description and len(obj_class.description) > len(existing.description or ""):
            existing.description = obj_class.description
        seen = {
            (str(chunk.get("doc_id") or chunk.get("docId")), str(chunk.get("chunk_id") or chunk.get("chunkId")))
            for chunk in existing.relevant_documentations
            if isinstance(chunk, dict)
        }
        for chunk in obj_class.relevant_documentations:
            pair = (str(chunk.get("doc_id") or chunk.get("docId")), str(chunk.get("chunk_id") or chunk.get("chunkId")))
            if pair not in seen:
                existing.relevant_documentations.append(chunk)
                seen.add(pair)
    return sorted(by_name.values(), key=lambda item: (confidence_order_key(item.confidence), item.name.lower()))


def _build_schema_heuristics(tables: list[dict[str, Any]]) -> str:
    table_summaries = []
    for table in tables:
        raw_columns = table.get("columns")
        columns = raw_columns if isinstance(raw_columns, list) else []
        table_summaries.append(
            {
                "table": table.get("table"),
                "objectClassCandidate": object_class_name_from_table(str(table.get("table") or "")),
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "primaryKey": column.get("primaryKey"),
                        "nullable": column.get("nullable"),
                    }
                    for column in columns[:30]
                    if isinstance(column, dict)
                ],
            }
        )
    return json.dumps(table_summaries, ensure_ascii=False, indent=2)


def _build_documentation_context(doc_items: list[dict]) -> str:
    chunks: list[str] = []
    for item in doc_items[:12]:
        content = str(item.get("content") or "")
        if not content.strip():
            continue
        chunks.append(
            "\n".join(
                [
                    f"summary: {item.get('summary') or ''}",
                    f"tags: {(item.get('@metadata') or {}).get('tags') or ''}",
                    content[:4000],
                ]
            )
        )
    return "\n\n---\n\n".join(chunks)


async def _detect_domain_classes_with_llm(
    *,
    tables: list[dict[str, Any]],
    doc_items: list[dict],
) -> list[ExtendedObjectClass]:
    if not tables and not doc_items:
        return []

    chain = build_structured_chain(
        sql_object_class_system_prompt,
        sql_object_class_user_prompt,
        ObjectClassesExtendedResponse,
    )
    result = await chain.ainvoke(
        {
            "schema_heuristics": _build_schema_heuristics(tables),
            "documentation_context": _build_documentation_context(doc_items),
        }
    )
    return result.object_classes if isinstance(result, ObjectClassesExtendedResponse) else []


async def extract_sql_object_classes(doc_items: list[dict], job_id: UUID) -> dict[str, Any]:
    """
    Extract database connector object classes.

    The pipeline mirrors SCIM shape: deterministic schema heuristics first, then
    one LLM call to keep only domain-specific object classes.
    """
    await update_job_progress(
        job_id,
        total_processing=len(doc_items) or 1,
        processing_completed=0,
        message="Processing SQL schema heuristics",
    )

    tables = collect_sql_tables(doc_items)
    heuristic_classes = [_object_class_from_table(table) for table in tables if _is_probably_domain_table(table)]

    llm_classes: list[ExtendedObjectClass] = []
    if tables or doc_items:
        try:
            llm_classes = await _detect_domain_classes_with_llm(tables=tables, doc_items=doc_items)
        except Exception as exc:
            logger.warning(
                "[SQL:ObjectClasses] Domain object-class LLM detection failed; using deterministic table heuristics. error=%s",
                type(exc).__name__,
            )

    llm_final_classes = [
        FinalObjectClass(
            name=obj_class.name,
            relevant=RelevantLevel.TRUE,
            confidence=ConfidenceLevel.MEDIUM,
            superclass=obj_class.superclass,
            abstract=bool(obj_class.abstract),
            embedded=bool(obj_class.embedded),
            description=obj_class.description,
        )
        for obj_class in llm_classes
    ]
    final_classes = _merge_sql_object_classes(heuristic_classes + llm_final_classes)
    relevant_chunks = build_relevant_chunks_from_doc_items(doc_items)

    await update_job_progress(
        job_id,
        processing_completed=len(doc_items) or 1,
        message=f"SQL object-class extraction complete: {len(final_classes)} classes",
    )

    return {
        "result": {"objectClasses": [obj_class.model_dump(by_alias=True, mode="json") for obj_class in final_classes]},
        "relevantDocumentations": relevant_chunks,
    }
