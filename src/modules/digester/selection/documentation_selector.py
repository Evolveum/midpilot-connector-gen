# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Documentation selection for object-class scoped attribute and endpoint extraction.

The selector loads the session's documentation once and derives every attempt the
job may make from that one snapshot: the primary chunks, the broader fallback (which
never repeats a primary chunk) and, for SCIM, the conndev documents the baseline is
built from. The result is stored in the job input as a
:class:`~src.documents.selection.DocumentationSelection`, so the worker never reads
the session's documentation again.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.documents.filtering.filter import load_documentation_items, select_documentation_items
from src.documents.normalize import normalize_object_class_name
from src.documents.selection import DocumentationSelection, SelectionRole
from src.modules.digester.entities.object_classes import resolve_object_class
from src.modules.digester.errors import RelevantChunksNotFoundError
from src.modules.digester.extractors.sql.schema import collect_sql_tables, tables_for_object_class
from src.modules.digester.schemas.common import ChunkReference
from src.modules.digester.selection.criteria import DEFAULT_CRITERIA, ENDPOINT_CRITERIA
from src.modules.digester.selection.doc_chunk import (
    build_chunk_references_from_doc_items,
    build_chunk_references_from_mappings,
    exclude_doc_items_by_chunk_id,
)
from src.session.info_metadata import get_session_base_api_url
from src.shared.coerce import is_true
from src.shared.content_types import is_conndev_documentation_item
from src.shared.enums import ApiType
from src.shared.session_keys import OBJECT_CLASSES


@dataclass(frozen=True)
class DocumentationSelectionPlan:
    """What an attribute or endpoint job stores: its documentation selection and request context."""

    selection: DocumentationSelection
    relevant_chunk_count: int
    """Chunks the first attempt reads (for SQL: the chunks evidencing the object class)."""
    base_api_url: str = ""
    object_class_flags: Dict[str, bool] = field(default_factory=dict)


class DocumentationSelector:
    """Build documentation selection plans for object-class scoped extraction jobs."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        load_corpus: Callable[..., Awaitable[List[Dict[str, Any]]]] | None = None,
        get_base_url: Callable[[UUID, ApiType | None], Awaitable[str]] | None = None,
        relevant_repo_factory: Callable[[AsyncSession], Any] | None = None,
    ):
        self._db = db
        self._load_corpus = load_corpus or load_documentation_items
        self._get_base_url = get_base_url or get_session_base_api_url
        self._relevant_repo_factory = relevant_repo_factory or RelevantChunkRepository

    async def build_attribute_plan(
        self,
        repo: Any,
        session_id: UUID,
        object_class: str,
        protocol: ApiType,
    ) -> DocumentationSelectionPlan:
        target_object_class = await resolve_object_class(repo, session_id, object_class)
        corpus = await self._load_corpus(session_id, db=self._db)

        if protocol == ApiType.SQL:
            # SQL joins physical schemas and conndev exports across the whole documentation,
            # so it keeps reading all of it; the object-class evidence only gates the job.
            sql_refs = await self._load_sql_object_class_chunk_refs(session_id, object_class, corpus)
            if not sql_refs:
                raise RelevantChunksNotFoundError(object_class, "attributes")
            return DocumentationSelectionPlan(
                selection=DocumentationSelection.from_corpus(corpus, {SelectionRole.SQL_SCHEMA: corpus}),
                relevant_chunk_count=len(sql_refs),
            )

        is_scim = protocol == ApiType.SCIM
        criteria = DEFAULT_CRITERIA.model_copy()
        normalized_name = normalize_object_class_name(object_class)
        criteria.allowed_tags = [[normalized_name, f"{normalized_name}s"]]
        primary_refs = build_chunk_references_from_doc_items(select_documentation_items(corpus, criteria))

        if not primary_refs and is_scim:
            primary_refs = await self._load_scim_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                target_object_class=target_object_class,
                include_superclass=True,
            )

        if not primary_refs and not is_scim:
            raise RelevantChunksNotFoundError(object_class, "attributes")

        fallback_items = select_documentation_items(corpus, DEFAULT_CRITERIA)
        return self._plan(corpus, primary_refs, fallback_items, is_scim=is_scim)

    async def build_endpoint_plan(
        self,
        repo: Any,
        session_id: UUID,
        object_class: str,
        protocol: ApiType,
        api_type_override: ApiType | None = None,
    ) -> DocumentationSelectionPlan:
        """
        Select documentation for REST/SCIM endpoint extraction.

        SQL never reaches this plan: a database connector has no endpoints, so the request is
        rejected in orchestration before a job exists. ``api_type_override`` only scopes the
        base URL lookup, exactly as an explicit ``apiType`` request parameter always did.
        """
        target_object_class = await resolve_object_class(repo, session_id, object_class)
        base_api_url = await self._get_base_url(session_id, api_type_override)
        corpus = await self._load_corpus(session_id, db=self._db)
        is_scim = protocol == ApiType.SCIM
        object_class_flags = _endpoint_object_class_flags(target_object_class) if is_scim else {}

        criteria = ENDPOINT_CRITERIA.model_copy()
        criteria.allowed_tags = [[normalize_object_class_name(object_class)], ["endpoint", "endpoints"]]
        default_items = select_documentation_items(corpus, DEFAULT_CRITERIA)
        primary_items = select_documentation_items(corpus, criteria) or default_items

        primary_refs = build_chunk_references_from_doc_items(primary_items)
        if not primary_refs and is_scim:
            primary_refs = await self._load_scim_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                target_object_class=target_object_class,
                include_superclass=False,
            )

        if not primary_refs and not is_scim:
            raise RelevantChunksNotFoundError(object_class, "endpoints")

        # The SCIM documentation fallback reads scraped documentation only; the conndev
        # contracts are already covered by deterministic pregeneration.
        fallback_items = (
            [item for item in default_items if not is_conndev_documentation_item(item)] if is_scim else default_items
        )
        plan = self._plan(corpus, primary_refs, fallback_items, is_scim=is_scim)
        return DocumentationSelectionPlan(
            selection=plan.selection,
            relevant_chunk_count=plan.relevant_chunk_count,
            base_api_url=base_api_url,
            object_class_flags=object_class_flags,
        )

    @staticmethod
    def _plan(
        corpus: List[Dict[str, Any]],
        primary_refs: List[ChunkReference],
        fallback_items: List[Dict[str, Any]],
        *,
        is_scim: bool,
    ) -> DocumentationSelectionPlan:
        """Store primary, fallback (minus primary chunks) and the SCIM baseline from one corpus."""
        primary_chunk_ids = {reference.chunk_id for reference in primary_refs}
        roles: Dict[SelectionRole, Sequence[Mapping[str, Any]]] = {
            SelectionRole.PRIMARY: [reference.to_internal_dict() for reference in primary_refs],
            SelectionRole.FALLBACK: exclude_doc_items_by_chunk_id(fallback_items, primary_chunk_ids),
        }
        if is_scim:
            roles[SelectionRole.SCIM_BASELINE] = [item for item in corpus if is_conndev_documentation_item(item)]
        selection = DocumentationSelection.from_corpus(corpus, roles)
        return DocumentationSelectionPlan(selection=selection, relevant_chunk_count=len(selection.primary))

    async def _load_sql_object_class_chunk_refs(
        self,
        session_id: UUID,
        object_class: str,
        doc_items: List[Dict[str, Any]],
    ) -> List[ChunkReference]:
        relevant_repo = self._relevant_repo_factory(self._db)
        by_entity = await relevant_repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key=OBJECT_CLASSES.output,
        )

        normalized_name = normalize_object_class_name(object_class)
        chunk_refs = build_chunk_references_from_mappings(by_entity.get(normalized_name, []))
        if chunk_refs:
            return chunk_refs

        selected_tables = tables_for_object_class(collect_sql_tables(doc_items), object_class)
        table_refs: List[Dict[str, Any]] = []
        for table in selected_tables:
            relevant_documentations = table.get("relevantDocumentations")
            if isinstance(relevant_documentations, list):
                table_refs.extend(chunk for chunk in relevant_documentations if isinstance(chunk, dict))

        chunk_refs = build_chunk_references_from_mappings(table_refs)
        if chunk_refs:
            return chunk_refs

        return build_chunk_references_from_doc_items(_select_sql_schema_doc_items(doc_items))

    async def _load_scim_object_class_chunk_refs(
        self,
        session_id: UUID,
        object_class: str,
        target_object_class: Dict[str, Any],
        include_superclass: bool,
    ) -> List[ChunkReference]:
        relevant_repo = self._relevant_repo_factory(self._db)
        by_entity = await relevant_repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key=OBJECT_CLASSES.output,
        )

        entity_keys = [normalize_object_class_name(object_class)]
        superclass = target_object_class.get("superclass")
        if include_superclass and isinstance(superclass, str) and superclass.strip():
            entity_keys.append(normalize_object_class_name(superclass))

        chunks: List[Dict[str, Any]] = []
        for entity_key in entity_keys:
            chunks.extend(by_entity.get(entity_key, []))

        return build_chunk_references_from_mappings(chunks)


def _select_sql_schema_doc_items(doc_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sql_items: List[Dict[str, Any]] = []
    for item in doc_items:
        metadata = item.get("@metadata") or {}
        content_type = str(metadata.get("content_type") or "").lower()
        if "sql" in content_type:
            sql_items.append(item)
            continue
        if collect_sql_tables([item]):
            sql_items.append(item)
    return sql_items


def _endpoint_object_class_flags(object_class: Dict[str, Any]) -> Dict[str, bool]:
    """Keep endpoint-relevant structural state explicit in the job input/cache identity."""
    return {
        "embedded": is_true(object_class.get("embedded")),
        "abstract": is_true(object_class.get("abstract")),
    }
