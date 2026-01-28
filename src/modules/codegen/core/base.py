# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union, cast
from uuid import UUID

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from ....common.chunks import normalize_to_text
from ....common.database.config import async_session_maker
from ....common.database.repositories.session_repository import SessionRepository
from ....common.enums import JobStage
from ....common.jobs import (
    append_job_error,
    increment_processed_documents,
    update_job_progress,
)
from ....common.langfuse import langfuse_handler
from ....common.llm import get_default_llm, make_basic_chain
from ...digester.schema import EndpointsResponse, ObjectClassSchemaResponse
from ..utils.postprocess import _coerce_llm_text, strip_markdown_fences

logger = logging.getLogger(__name__)

AttributesPayload = Union[ObjectClassSchemaResponse, Mapping[str, Any]]
EndpointsPayload = Union[EndpointsResponse, Mapping[str, Any]]


@dataclass
class OperationConfig:
    """Configuration for a specific code generation operation."""

    operation_name: str  # e.g., "Search", "Create", "Update", "Delete", "Relation"
    system_prompt: str
    user_prompt: str
    default_scaffold: str  # Fallback code when generation fails
    logger_prefix: str  # For logging, e.g., "Codegen:Search"

    extra_prompt_vars: Dict[str, Any] = field(default_factory=dict)


class ChunkProcessor:
    """Handles chunk selection and processing logic."""

    @staticmethod
    def build_chunks_from_pairs(
        relevant_chunk_pairs: List[Dict[str, Any]],
        documentation_items: List[Dict[str, Any]],
        logger_prefix: str,
    ) -> tuple[List[str], List[Optional[str]], Dict[str, int], List[str]]:
        """
        Build chunks using per-document selection (when pairs + items are provided).
        Uses pre-chunked documentation items directly without re-chunking.

        Returns:
            - chunks: List of text chunks
            - provenance_doc_uuid: List of doc UUIDs corresponding to each chunk
            - per_doc_selected_counts: Dict mapping doc_uuid to chunk count
            - docs_included: List of doc UUIDs included
        """
        chunks: List[str] = []
        provenance_doc_uuid: List[Optional[str]] = []
        per_doc_selected_counts: Dict[str, int] = {}
        docs_included: List[str] = []

        # Build chunk map by UUID - documentation_items are already chunked
        chunks_by_uuid: Dict[str, Dict[str, Any]] = {}
        for item in documentation_items:
            try:
                uid = item.get("uuid") or item.get("id")
                if isinstance(uid, str):
                    chunks_by_uuid[uid] = item
            except Exception:
                continue

        # Process pairs in order - each pair references a specific chunk by its ID
        doc_counts: Dict[str, int] = {}
        seen_docs: List[str] = []

        for p in relevant_chunk_pairs:
            doc_uuid = p.get("docUuid")
            if not isinstance(doc_uuid, str):
                continue

            chunk_item = chunks_by_uuid.get(doc_uuid)
            if not chunk_item:
                logger.warning("%s Missing chunk for doc_uuid=%s", logger_prefix, doc_uuid)
                continue

            content = chunk_item.get("content")
            if not isinstance(content, str):
                continue

            # Add chunk
            chunks.append(normalize_to_text(content))
            provenance_doc_uuid.append(doc_uuid)

            # Track per-document counts
            if doc_uuid not in doc_counts:
                doc_counts[doc_uuid] = 0
                seen_docs.append(doc_uuid)
            doc_counts[doc_uuid] += 1

        per_doc_selected_counts = doc_counts
        docs_included = seen_docs

        logger.info(
            "%s Using %d pre-chunked documentation items from %d unique documents",
            logger_prefix,
            len(chunks),
            len(docs_included),
        )

        return chunks, provenance_doc_uuid, per_doc_selected_counts, docs_included


class BaseGroovyGenerator(ABC):
    """
    Base class for Groovy code generation with common chunk processing logic.

    This class implements the Template Method pattern, allowing subclasses
    to customize specific parts while reusing the core generation logic.
    """

    def __init__(self, config: OperationConfig):
        self.config = config

    @abstractmethod
    def prepare_input_data(self, **kwargs) -> Dict[str, str]:
        """
        Prepare operation-specific input data for prompts.

        Must return a dict with string keys/values that will be passed to the LLM prompt.
        Example: {"attributes_json": "...", "endpoints_json": "..."}
        """
        pass

    @abstractmethod
    def get_initial_result(self, **kwargs) -> str:
        """
        Get the initial scaffold/result before processing chunks.

        Example: 'objectClass("User") {\\n search {}\\n}'
        """
        pass

    async def generate(
        self,
        *,
        session_id: Optional[UUID] = None,
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]] = None,
        job_id: UUID,
        **operation_specific_kwargs,
    ) -> str:
        """
        Main generation method using Template Method pattern.

        This method orchestrates the entire generation process:
        1. Load documentation items from DB
        2. Build chunks (using pre-chunked docs)
        3. Initialize progress tracking
        4. Process chunks iteratively with LLM
        5. Handle errors and return result
        """
        # Step 1: Load documentation items from session
        documentation_items = await self._load_documentation_items(session_id) if session_id else []

        # Step 2: Build chunks
        chunks, provenance_doc_uuid, per_doc_counts, docs_included = self._build_chunks(
            documentation_items=documentation_items,
            relevant_chunk_pairs=relevant_chunk_pairs,
        )

        if not chunks:
            logger.warning("%s No chunks to process", self.config.logger_prefix)
            return self.config.default_scaffold

        # Step 2: Initialize progress
        await self._initialize_progress(job_id, chunks, docs_included)

        # Step 3: Prepare input data and LLM chain
        input_data = self.prepare_input_data(**operation_specific_kwargs)
        chain = self._build_llm_chain(len(chunks))

        # Step 4: Process chunks iteratively
        result = self.get_initial_result(**operation_specific_kwargs)
        result = await self._process_chunks(
            chunks=chunks,
            provenance_doc_uuid=provenance_doc_uuid,
            per_doc_counts=per_doc_counts,
            docs_included=docs_included,
            input_data=input_data,
            chain=chain,
            job_id=job_id,
            initial_result=result,
        )

        if not result:
            logger.warning("%s No code produced; returning default scaffold", self.config.logger_prefix)
            return self.config.default_scaffold

        return strip_markdown_fences(result)

    async def _load_documentation_items(self, session_id: UUID) -> List[Dict[str, Any]]:
        """Load documentation items from session."""
        async with async_session_maker() as db:
            repo = SessionRepository(db)
            doc_items = await repo.get_session_data(session_id, "documentationItems")
            return doc_items or []

    def _build_chunks(
        self,
        documentation_items: List[Dict[str, Any]],
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]],
    ) -> tuple[List[str], List[Optional[str]], Dict[str, int], List[str]]:
        """Build chunks from pre-chunked documentation items."""
        if not documentation_items:
            logger.warning("%s No documentation items available", self.config.logger_prefix)
            return [], [], {}, []

        if relevant_chunk_pairs:
            # Use selected chunks based on pairs
            chunks, provenance, per_doc_counts, docs = ChunkProcessor.build_chunks_from_pairs(
                relevant_chunk_pairs, documentation_items, self.config.logger_prefix
            )
            return chunks, provenance, per_doc_counts, docs
        else:
            # Use all documentation items directly
            chunks = [normalize_to_text(item.get("content", "")) for item in documentation_items]
            provenance = [item.get("uuid") or item.get("id") for item in documentation_items]
            logger.info("%s Using all %d pre-chunked documentation items", self.config.logger_prefix, len(chunks))
            return chunks, provenance, {}, []

    async def _initialize_progress(
        self,
        job_id: UUID,
        chunks: List[str],
        docs_included: List[str],
    ):
        """Initialize job progress tracking."""
        total_chunks = len(chunks)
        logger.info("%s Processing %d chunks", self.config.logger_prefix, total_chunks)

        # Use document count if available, otherwise use chunk count as fallback
        total_count = len(docs_included) if docs_included else total_chunks

        await update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            total_processing=total_count,
            processing_completed=0,
            message="Processing chunks and try to extract relevant information",
        )

    def _build_llm_chain(self, total_chunks: int):
        """Build the LangChain chain for LLM invocation."""
        llm = get_default_llm()
        prompt = ChatPromptTemplate.from_messages(
            [("system", self.config.system_prompt), ("human", self.config.user_prompt)]
        )

        partial_vars: Dict[str, Any] = {"total": total_chunks}
        partial_vars.update(self.config.extra_prompt_vars)

        prompt = prompt.partial(**partial_vars)
        return make_basic_chain(prompt, llm, StrOutputParser())

    async def _process_chunks(
        self,
        chunks: List[str],
        provenance_doc_uuid: List[Optional[str]],
        per_doc_counts: Dict[str, int],
        docs_included: List[str],
        input_data: Dict[str, str],
        chain,
        job_id: UUID,
        initial_result: str,
    ) -> str:
        """Process chunks iteratively with LLM."""
        result = initial_result
        total_chunks = len(chunks)
        current_doc_uuid: Optional[str] = None
        current_doc_chunks_remaining: int = 0

        for idx, chunk in enumerate(chunks, start=1):
            doc_uuid = provenance_doc_uuid[idx - 1] if idx - 1 < len(provenance_doc_uuid) else None

            try:
                # Update current document if in per-doc mode
                if per_doc_counts and docs_included and isinstance(doc_uuid, str):
                    if current_doc_uuid != doc_uuid:
                        total_for_doc = per_doc_counts.get(doc_uuid, 0)
                        current_doc_uuid = doc_uuid
                        current_doc_chunks_remaining = total_for_doc

                # Log progress
                if doc_uuid:
                    logger.info(
                        "%s LLM call %d/%d (doc_uuid: %s)",
                        self.config.logger_prefix,
                        idx,
                        total_chunks,
                        doc_uuid,
                    )
                else:
                    logger.info("%s LLM call %d/%d", self.config.logger_prefix, idx, total_chunks)

                # Invoke LLM
                prompt_vars = {"idx": idx, "chunk": chunk, "result": result}
                prompt_vars.update(input_data)

                response = await chain.ainvoke(prompt_vars, config=RunnableConfig(callbacks=[langfuse_handler]))
                code = _coerce_llm_text(response).strip()

                if code:
                    result = strip_markdown_fences(code)

            except Exception as exc:
                logger.error("%s Chunk %d failed: %s", self.config.logger_prefix, idx, exc)
                append_job_error(job_id, f"[{self.config.logger_prefix}] Chunk {idx}/{total_chunks} failed: {exc}")
                continue

            finally:
                # Handle progress tracking based on mode
                if per_doc_counts and docs_included and isinstance(doc_uuid, str):
                    # Per-document mode: increment when document is complete
                    current_doc_chunks_remaining = max(0, current_doc_chunks_remaining - 1)
                    if current_doc_chunks_remaining == 0:
                        await increment_processed_documents(job_id, delta=1)
                        logger.info("%s Completed document %s", self.config.logger_prefix, doc_uuid)
                else:
                    # Fallback mode (no per-doc tracking): increment per chunk
                    await increment_processed_documents(job_id, delta=1)

        return result


def attributes_to_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """Convert attributes payload to list of records."""
    if isinstance(payload, ObjectClassSchemaResponse):
        records: List[Dict[str, Any]] = []
        for name, info in (payload.attributes or {}).items():
            item: Dict[str, Any] = {"name": name}
            item.update(info.model_dump())
            records.append(item)
        return records

    if isinstance(payload, Mapping):
        if "attributes" in payload and isinstance(payload["attributes"], Mapping):
            attrs_map: Mapping[str, Any] = cast(Mapping[str, Any], payload["attributes"])
        else:
            attrs_map = payload

        records_alt: List[Dict[str, Any]] = []
        for name, info in attrs_map.items():
            item_alt: Dict[str, Any] = {"name": name}
            if isinstance(info, Mapping):
                item_alt.update(dict(info))
            records_alt.append(item_alt)
        return records_alt
    return []


def endpoints_to_records(payload: EndpointsPayload) -> List[Dict[str, Any]]:
    """Convert endpoints payload to list of records."""
    if isinstance(payload, EndpointsResponse):
        return [cast(Dict[str, Any], ep.model_dump()) for ep in (payload.endpoints or [])]

    if isinstance(payload, Mapping):
        if "endpoints" in payload and isinstance(payload["endpoints"], list):
            return list(payload["endpoints"])
        if all(k in payload for k in ("path", "method", "description")):
            return [dict(payload)]
    return []
