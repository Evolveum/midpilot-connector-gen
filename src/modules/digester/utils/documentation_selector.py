# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.chunk_filter.filter import filter_documentation_items
from src.common.database.repositories.relevant_chunk_repository import RelevantChunkRepository
from src.common.errors import (
    InvalidObjectClassesOutputError,
    ObjectClassesNotFoundError,
    ObjectClassNotFoundError,
    RelevantChunksNotFoundError,
)
from src.common.session.session import get_session_documentation
from src.common.utils.normalize import normalize_object_class_name
from src.common.utils.session_info_metadata import (
    get_session_api_types,
    get_session_base_api_url,
    is_scim_api,
    is_sql_api,
)
from src.modules.digester.extractors.sql.schema import collect_sql_tables, tables_for_object_class
from src.modules.digester.schemas.common import ChunkReference
from src.modules.digester.utils.criteria import DEFAULT_CRITERIA, ENDPOINT_CRITERIA
from src.modules.digester.utils.doc_chunk import (
    build_chunk_references_from_doc_items,
    build_chunk_references_from_mappings,
)
from src.modules.digester.utils.object_classes import find_object_class


@dataclass(frozen=True)
class DocumentationSelection:
    doc_items: List[Dict[str, Any]]
    chunk_references: List[ChunkReference]
    base_api_url: str = ""

    @property
    def relevant_chunks(self) -> List[Dict[str, str]]:
        return [chunk_ref.to_internal_dict() for chunk_ref in self.chunk_references]


class DocumentationSelector:
    """Build documentation selection plans for object-class scoped extraction jobs."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        filter_items: Callable[..., Awaitable[List[Dict[str, Any]]]] | None = None,
        get_documentation: Callable[..., Awaitable[List[Dict[str, Any]]]] | None = None,
        get_api_types: Callable[[UUID], Awaitable[Any]] | None = None,
        get_base_url: Callable[[UUID], Awaitable[str]] | None = None,
        relevant_repo_factory: Callable[[AsyncSession], Any] | None = None,
    ):
        self._db = db
        self._filter_items = filter_items or filter_documentation_items
        self._get_documentation = get_documentation or get_session_documentation
        self._get_api_types = get_api_types or get_session_api_types
        self._get_base_url = get_base_url or get_session_base_api_url
        self._relevant_repo_factory = relevant_repo_factory or RelevantChunkRepository

    async def build_attribute_plan(
        self,
        repo: Any,
        session_id: UUID,
        object_class: str,
    ) -> DocumentationSelection:
        target_object_class = await self._get_target_object_class(repo, session_id, object_class)
        api_types = await self._get_api_types(session_id)
        is_scim = is_scim_api(api_types)
        is_sql = is_sql_api(api_types)

        if is_sql:
            doc_items = await self._get_documentation(session_id, db=self._db)
            chunk_refs = await self._load_sql_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                doc_items=doc_items,
            )
            if not chunk_refs:
                raise RelevantChunksNotFoundError(object_class, "attributes")
            return DocumentationSelection(doc_items=doc_items, chunk_references=chunk_refs)

        criteria = DEFAULT_CRITERIA.model_copy()
        normalized_name = normalize_object_class_name(object_class)
        criteria.allowed_tags = [[normalized_name, f"{normalized_name}s"]]

        filtered_items = await self._filter_items(criteria, session_id, db=self._db)
        chunk_refs = build_chunk_references_from_doc_items(filtered_items)

        if not chunk_refs and is_scim:
            chunk_refs = await self._load_scim_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                target_object_class=target_object_class,
                include_superclass=True,
            )

        if not chunk_refs and not is_scim:
            raise RelevantChunksNotFoundError(object_class, "attributes")

        return DocumentationSelection(
            doc_items=await self._get_documentation(session_id, db=self._db),
            chunk_references=chunk_refs,
        )

    async def build_endpoint_plan(
        self,
        repo: Any,
        session_id: UUID,
        object_class: str,
    ) -> DocumentationSelection:
        target_object_class = await self._get_target_object_class(repo, session_id, object_class)
        base_api_url = await self._get_base_url(session_id)
        api_types = await self._get_api_types(session_id)
        is_scim = is_scim_api(api_types)
        is_sql = is_sql_api(api_types)

        if is_sql:
            doc_items = await self._get_documentation(session_id, db=self._db)
            chunk_refs = await self._load_sql_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                doc_items=doc_items,
            )
            if not chunk_refs:
                raise RelevantChunksNotFoundError(object_class, "endpoints")
            return DocumentationSelection(doc_items=doc_items, chunk_references=chunk_refs, base_api_url=base_api_url)

        criteria = ENDPOINT_CRITERIA.model_copy()
        criteria.allowed_tags = [[normalize_object_class_name(object_class)], ["endpoint", "endpoints"]]
        filtered_items = await self._filter_items(criteria, session_id, db=self._db)

        if not filtered_items:
            filtered_items = await self._filter_items(DEFAULT_CRITERIA, session_id, db=self._db)

        chunk_refs = build_chunk_references_from_doc_items(filtered_items)
        if not chunk_refs and is_scim:
            chunk_refs = await self._load_scim_object_class_chunk_refs(
                session_id=session_id,
                object_class=object_class,
                target_object_class=target_object_class,
                include_superclass=False,
            )

        if not chunk_refs and not is_scim:
            raise RelevantChunksNotFoundError(object_class, "endpoints")

        return DocumentationSelection(
            doc_items=await self._get_documentation(session_id, db=self._db),
            chunk_references=chunk_refs,
            base_api_url=base_api_url,
        )

    async def _get_target_object_class(self, repo: Any, session_id: UUID, object_class: str) -> Dict[str, Any]:
        object_classes_output = await repo.get_session_data(session_id, "objectClassesOutput")
        if not object_classes_output or not isinstance(object_classes_output, dict):
            raise ObjectClassesNotFoundError(session_id)

        object_classes = object_classes_output.get("objectClasses", [])
        if not isinstance(object_classes, list):
            raise InvalidObjectClassesOutputError(session_id)

        target_object_class = find_object_class(object_classes, object_class)
        if not target_object_class:
            raise ObjectClassNotFoundError(object_class, session_id)

        return target_object_class

    async def _load_sql_object_class_chunk_refs(
        self,
        session_id: UUID,
        object_class: str,
        doc_items: List[Dict[str, Any]],
    ) -> List[ChunkReference]:
        relevant_repo = self._relevant_repo_factory(self._db)
        by_entity = await relevant_repo.get_relevant_chunks_grouped_by_entity(
            session_id=session_id,
            result_key="objectClassesOutput",
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
            result_key="objectClassesOutput",
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
