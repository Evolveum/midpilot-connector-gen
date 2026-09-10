# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from typing import Any
from uuid import UUID

from src.jobs import update_job_progress
from src.modules.digester.entities.object_classes import build_attribute_result
from src.modules.digester.errors import SqlPhysicalSchemaNotFoundError
from src.modules.digester.extractors.sql.conndev_schema import SqlTableSource, sql_table_source
from src.modules.digester.extractors.sql.schema import (
    collect_sql_tables,
    sql_type_to_attribute_type,
    tables_for_object_class,
)
from src.shared.coerce import as_nonempty_str
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


def _column_attribute_type(column: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve the public type/format exclusively from the SQL/JDBC type."""
    return sql_type_to_attribute_type(column.get("type"))


def _attribute_foreign_key(column: dict[str, Any]) -> dict[str, str] | None:
    """Keep only the typed foreign-key shape understood by downstream SQL codegen."""
    foreign_key = column.get("foreignKey")
    if not isinstance(foreign_key, dict):
        return None

    referenced_table = as_nonempty_str(foreign_key.get("referencedTable"))
    referenced_column = as_nonempty_str(foreign_key.get("referencedColumn"))
    if referenced_table is None or referenced_column is None:
        return None

    normalized = {
        "referencedTable": referenced_table,
        "referencedColumn": referenced_column,
    }
    constraint_name = as_nonempty_str(foreign_key.get("constraintName"))
    if constraint_name is not None:
        normalized["constraintName"] = constraint_name
    return normalized


def _attribute_from_column(column: dict[str, Any], table: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    column_name = as_nonempty_str(column.get("name"))
    if column_name is None:
        return None

    attr_type, attr_format = _column_attribute_type(column)

    mandatory = column.get("mandatory")
    if mandatory is None and column.get("nullable") is not None:
        mandatory = not bool(column["nullable"])

    is_view = str(table.get("tableType") or "").strip().casefold() == "view"
    updatable = not bool(column.get("primaryKey")) and not is_view
    creatable = not bool(column.get("generated")) and not is_view

    relevant_documentations = table.get("relevantDocumentations")
    if not isinstance(relevant_documentations, list):
        relevant_documentations = []
    payload = {
        "type": attr_type,
        "format": attr_format,
        "description": _column_description(column_name, table),
        "mandatory": mandatory,
        "updatable": bool(updatable),
        "creatable": bool(creatable),
        "readable": True,
        "multivalue": False,
        "returnedByDefault": True,
        "table": table.get("table"),
        "column": column_name,
        "primaryKey": column.get("primaryKey"),
        "foreignKey": _attribute_foreign_key(column),
        "relevantDocumentations": relevant_documentations,
    }
    database_catalog = as_nonempty_str(table.get("databaseCatalog"))
    if database_catalog is not None:
        payload["databaseCatalog"] = database_catalog
    database_schema = as_nonempty_str(table.get("databaseSchema"))
    if database_schema is not None:
        payload["databaseSchema"] = database_schema
    database_type = as_nonempty_str(column.get("type"))
    if database_type is not None:
        payload["databaseType"] = database_type
    for source_key, target_key in (
        ("nullable", "nullable"),
        ("unique", "unique"),
        ("generated", "generated"),
        ("default", "defaultValue"),
    ):
        if source_key in column:
            payload[target_key] = column[source_key]
    return column_name, payload


def _column_description(name: str, table: dict[str, Any]) -> str:
    database_schema = str(table.get("databaseSchema") or "").strip()
    qualified_table = f"{database_schema}.{table.get('table')}" if database_schema else str(table.get("table"))
    return f"Column '{name}' from table '{qualified_table}'."


async def extract_sql_attributes(
    doc_items: list[dict],
    object_class: str,
    job_id: UUID,
) -> dict[str, Any]:
    """Build SQL attributes deterministically from selected schema tables."""
    await update_job_progress(
        job_id,
        stage=JobStage.processing,
        total_processing=len(doc_items) or 1,
        processing_completed=0,
        message=f"Extracting SQL columns for {object_class}",
    )

    tables = tables_for_object_class(collect_sql_tables(doc_items), object_class)
    has_projection = any(isinstance(table.get("connectorObjectClass"), dict) for table in tables)
    physical_tables = [
        table
        for table in tables
        if sql_table_source(table) is not SqlTableSource.CONNDEV_OBJECT_CLASS
        and any(isinstance(column, dict) for column in table.get("columns", []))
    ]
    if has_projection and not physical_tables:
        raise SqlPhysicalSchemaNotFoundError(object_class)

    selected_tables = physical_tables or tables
    attributes: dict[str, dict[str, Any]] = {}
    relevant_chunks: list[dict[str, Any]] = []
    seen_chunks: set[tuple[str, str]] = set()

    for table in selected_tables:
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
        stage=JobStage.schema_ready,
        processing_completed=len(doc_items) or 1,
        message=f"SQL attribute extraction complete: {len(attributes)} attributes",
    )
    result = build_attribute_result(attributes, relevant_chunks)
    if selected_tables:
        result["result"]["sqlContext"] = _sql_context(selected_tables[0])
    return result


def _sql_context(table: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded SQL context without folding projection data into columns."""
    physical_table = {"table": table["table"]}
    for key in ("databaseCatalog", "databaseSchema", "tableType"):
        value = as_nonempty_str(table.get(key))
        if value is not None:
            physical_table[key] = value

    context: dict[str, Any] = {"physicalTable": physical_table}
    projection = table.get("connectorObjectClass")
    if isinstance(projection, dict):
        normalized_projection = dict(projection)
        raw_attributes = projection.get("attributes")
        if isinstance(raw_attributes, list):
            normalized_projection["attributes"] = [
                {**attribute, "column": attribute.get("column")}
                for attribute in raw_attributes
                if isinstance(attribute, dict)
            ]
        context["connectorObjectClass"] = normalized_projection
    return context
