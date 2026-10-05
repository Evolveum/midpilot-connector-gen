# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import sys
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Mapping, Optional, cast
from uuid import UUID

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import RunnableConfig

from src.config import config
from src.core.db import async_session_maker
from src.core.llm import (
    get_default_llm,
    make_basic_chain,
    raise_if_llm_unavailable,
    retry_on_transient_llm_error,
)
from src.core.observability.langfuse import langfuse_handler
from src.database.repositories.documentation_repository import DocumentationRepository
from src.documents.chunking import normalize_to_text
from src.jobs import (
    append_job_error,
    increment_processed_documents,
    update_job_progress,
)
from src.jobs.errors import JobClaimLostError
from src.modules.codegen.core.validation_feedback import GeneratedArtifact, GenerationState
from src.modules.codegen.enums import ConnectorCodeFormat
from src.modules.codegen.prompts.cleanup_prompts import (
    get_groovy_cleanup_system_prompt,
    get_groovy_cleanup_user_prompt,
    get_yaml_cleanup_system_prompt,
    get_yaml_cleanup_user_prompt,
)
from src.modules.codegen.prompts.validation_feedback_prompts import VALIDATION_FEEDBACK_SYSTEM_RULES
from src.modules.codegen.repair import (
    NO_CODE_GENERATED,
    NO_REPAIR_GENERATED,
    build_repair_prompt_vars,
    get_repair_initial_result,
)
from src.modules.codegen.schema import CodegenRepairContext, EndpointsPayload, OperationConfig
from src.modules.codegen.utils.connector_code_validation import (
    detect_connector_code_format,
    inspect_connector_code,
    log_validation_warnings,
)
from src.modules.codegen.utils.postprocess import coerce_llm_text, strip_markdown_fences
from src.modules.codegen.utils.prompt_records import strip_relevant_documentation_refs
from src.modules.digester.schemas import EndpointResponse
from src.shared.content_types import is_conndev_documentation_item
from src.shared.enums import JobStage

logger = logging.getLogger(__name__)


class ChunkProcessor:
    """Handles chunk selection and processing logic."""

    @staticmethod
    def exclude_conndev_contracts(
        documentation_items: List[Dict[str, Any]],
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]],
    ) -> tuple[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        """
        Drop the deterministic conndev contracts, and the pairs that select them.

        conndev contracts are generated from a machine-readable schema and are never
        LLM input. Dropping the selecting pairs as well keeps
        :meth:`build_chunks_from_pairs` from reporting a deliberately excluded chunk
        as a missing one. ``None`` pairs mean "no selection exists" and stay ``None``.
        """
        llm_documentation_items: List[Dict[str, Any]] = []
        excluded_chunk_ids: set[str] = set()
        for item in documentation_items:
            if is_conndev_documentation_item(item):
                chunk_id = item.get("chunkId")
                if isinstance(chunk_id, str):
                    excluded_chunk_ids.add(chunk_id)
                continue
            llm_documentation_items.append(item)

        if len(llm_documentation_items) < len(documentation_items):
            logger.info(
                "[Codegen:Chunks] Excluded %d conndev contract document(s) from codegen LLM chunks",
                len(documentation_items) - len(llm_documentation_items),
            )

        if relevant_chunk_pairs is None:
            return llm_documentation_items, None

        return llm_documentation_items, [
            pair
            for pair in relevant_chunk_pairs
            if (pair.get("chunk_id") or pair.get("chunkId")) not in excluded_chunk_ids
        ]

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
            - provenance_chunk_ids: List of chunk IDs corresponding to each chunk
            - per_chunk_selected_counts: Dict mapping chunk_id to selected chunk count
            - chunk_ids_included: List of included chunk IDs
        """
        chunks: List[str] = []
        provenance_chunk_ids: List[Optional[str]] = []

        # Build chunk map by UUID - documentation_items are already chunked
        chunks_by_uuid: Dict[str, Dict[str, Any]] = {}
        for item in documentation_items:
            uid = item.get("chunkId")
            if isinstance(uid, str):
                chunks_by_uuid[uid] = item

        # Process pairs in order - each pair references a specific chunk by its ID
        chunk_counts: Dict[str, int] = {}
        seen_chunk_ids: List[str] = []

        for p in relevant_chunk_pairs:
            chunk_id = p.get("chunk_id") or p.get("chunkId")
            if not isinstance(chunk_id, str):
                continue

            chunk_item = chunks_by_uuid.get(chunk_id)
            if not chunk_item:
                logger.warning("%s Missing chunk for chunk_id=%s", logger_prefix, chunk_id)
                continue

            content = chunk_item.get("content")
            if not isinstance(content, str):
                continue

            # Add chunk
            chunks.append(normalize_to_text(content))
            provenance_chunk_ids.append(chunk_id)

            # Track per-chunk-group counts
            if chunk_id not in chunk_counts:
                chunk_counts[chunk_id] = 0
                seen_chunk_ids.append(chunk_id)
            chunk_counts[chunk_id] += 1

        logger.info(
            "%s Using %d pre-chunked documentation items from %d unique chunk IDs",
            logger_prefix,
            len(chunks),
            len(seen_chunk_ids),
        )

        return chunks, provenance_chunk_ids, chunk_counts, seen_chunk_ids


class BaseGroovyGenerator(ABC):
    """
    Base class for connector code generation with common chunk processing logic.

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

    def normalize_generated_code(self, code: str) -> str:
        """Normalize a model candidate before validation; preserve artifacts by default."""
        return code

    async def generate(
        self,
        *,
        session_id: Optional[UUID] = None,
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]] = None,
        job_id: UUID,
        repair_context: Optional[CodegenRepairContext] = None,
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
        chunks, provenance_chunk_ids, per_chunk_counts, chunk_ids_included = self._build_chunks(
            documentation_items=documentation_items,
            relevant_chunk_pairs=relevant_chunk_pairs,
        )

        if (
            not chunks
            and self.config.context_only_for_conndev
            and self._has_relevant_conndev_contracts(
                documentation_items=documentation_items,
                relevant_chunk_pairs=relevant_chunk_pairs,
            )
        ):
            chunks = [""]
            provenance_chunk_ids = [None]
            per_chunk_counts = {}
            chunk_ids_included = []
            logger.info(
                "%s No LLM text chunks remain after conndev filtering; running one context-only generation pass",
                self.config.logger_prefix,
            )

        if not chunks and repair_context is not None:
            chunks = [""]
            provenance_chunk_ids = [None]
            per_chunk_counts = {}
            chunk_ids_included = []
            logger.info("%s Repair mode has no chunks; running single repair pass", self.config.logger_prefix)

        if not chunks:
            logger.warning("[Codegen:Generation] No chunks to process")
            await append_job_error(job_id, NO_CODE_GENERATED)
            return ""

        # Step 2: Initialize progress
        await self._initialize_progress(job_id, chunks, chunk_ids_included)

        # Step 3: Prepare input data and LLM chain
        input_data = self.prepare_input_data(**operation_specific_kwargs)
        input_data.update(build_repair_prompt_vars(repair_context))
        chain = self._build_llm_chain(len(chunks))

        # Step 4: Process chunks iteratively
        initial_result = get_repair_initial_result(repair_context=repair_context, fallback_result="")
        state = await self._process_chunks(
            chunks=chunks,
            provenance_chunk_ids=provenance_chunk_ids,
            per_chunk_counts=per_chunk_counts,
            chunk_ids_included=chunk_ids_included,
            input_data=input_data,
            chain=chain,
            job_id=job_id,
            initial_result=initial_result,
        )

        if state.pending is not None:
            await self._repair_final_validation(
                state=state, chain=chain, input_data=input_data, initial_result=initial_result, job_id=job_id
            )

        if state.accepted is None:
            await append_job_error(job_id, NO_REPAIR_GENERATED if repair_context else NO_CODE_GENERATED)
            return initial_result

        # Cleanup validates changed output and otherwise returns this already-validated artifact.
        result = await self._cleanup_generated_code(artifact=state.accepted, job_id=job_id)
        return strip_markdown_fences(result.code)

    async def _cleanup_generated_code(self, artifact: GeneratedArtifact, job_id: UUID) -> GeneratedArtifact:
        """
        Run one final LLM cleanup pass to remove TODO/comment-only scaffolding.

        Format-aware: a YAML artifact is cleaned with YAML-specific rules (preserving block-scalar
        indentation and meaningful empty mappings) rather than the Groovy cleanup prompt, and the
        prompt instructs the model to retain that format. The result passes the shared format-aware
        validator. Falls back to the original code if cleanup fails or produces invalid output.
        """
        code = artifact.code
        is_yaml = await asyncio.to_thread(detect_connector_code_format, code) is ConnectorCodeFormat.YAML
        system_prompt = get_yaml_cleanup_system_prompt if is_yaml else get_groovy_cleanup_system_prompt
        user_prompt = get_yaml_cleanup_user_prompt if is_yaml else get_groovy_cleanup_user_prompt
        prompt_var_name = "yaml_code" if is_yaml else "groovy_code"

        try:
            logger.info("[Codegen:Generation] Running final cleanup LLM pass for %s", self.config.operation_name)
            llm = get_default_llm()
            prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", user_prompt)])
            chain = make_basic_chain(prompt, llm, StrOutputParser())
            response = await self._invoke_generation_chain(
                chain,
                {prompt_var_name: code},
                context="cleanup",
                run_name_suffix=":Cleanup",
                max_attempts=1,
            )
            candidate = await self._evaluate_response(response, artifact)
            if candidate is None:
                logger.warning("[Codegen:Generation] Cleanup pass returned empty output; keeping previous code")
                return artifact

            validation_error = candidate.validation.first_error
            if validation_error is not None:
                error_message = f"{self.config.logger_prefix} Cleanup pass produced invalid output: {validation_error}"
                logger.warning("[Codegen:Generation] Cleanup pass produced invalid output: %s", validation_error)
                await append_job_error(job_id, error_message)
                return artifact

            return candidate

        except JobClaimLostError:
            raise
        except Exception as exc:
            error_message = f"{self.config.logger_prefix} Cleanup pass failed: {exc}"
            logger.exception("[Codegen:Generation] Cleanup pass failed")
            await append_job_error(job_id, error_message)
            return artifact

    async def _evaluate_response(self, response: Any, *known: GeneratedArtifact) -> GeneratedArtifact | None:
        candidate = await asyncio.to_thread(
            self.normalize_generated_code, strip_markdown_fences(coerce_llm_text(response))
        )
        if not candidate:
            return None
        for artifact in known:
            if artifact.code == candidate:
                return artifact
        report = await asyncio.to_thread(inspect_connector_code, candidate)
        log_validation_warnings(report)
        return GeneratedArtifact(candidate, report)

    async def _invoke_generation_chain(
        self,
        chain: Runnable[Dict[str, Any], str],
        prompt_vars: Dict[str, Any],
        *,
        context: str,
        run_name_suffix: str = "",
        max_attempts: int | None = None,
    ) -> str:
        """Share invocation and tracing; cleanup explicitly retains its single-attempt policy."""
        run_name = self.config.logger_prefix.strip("[]") + run_name_suffix
        return await retry_on_transient_llm_error(
            lambda: chain.ainvoke(
                prompt_vars,
                config=RunnableConfig(callbacks=[langfuse_handler], run_name=run_name),
            ),
            max_attempts=config.llm.transient_retry_attempts if max_attempts is None else max_attempts,
            base_delay=config.llm.transient_retry_base_delay_seconds,
            logger_prefix="[Codegen:Generation] ",
            context=context,
        )

    async def _record_candidate(
        self,
        state: GenerationState,
        candidate: GeneratedArtifact,
        *,
        chunk: str,
        index: int,
        context: str,
        job_id: UUID,
    ) -> None:
        had_feedback = state.pending is not None
        state.record(candidate, chunk=chunk, index=index)
        if candidate.validation.errors:
            error = candidate.validation.first_error
            logger.warning("[Codegen:Generation] Invalid output after %s: %s", context, error)
            await append_job_error(job_id, f"{self.config.logger_prefix} Invalid output after {context}: {error}")
        elif had_feedback and state.pending is None:
            logger.info("[Codegen:Generation] Local validation feedback resolved after %s", context)

    async def _repair_final_validation(
        self,
        *,
        state: GenerationState,
        chain: Runnable[Dict[str, Any], str],
        input_data: Dict[str, str],
        initial_result: str,
        job_id: UUID,
    ) -> None:
        feedback = state.pending
        if feedback is None:
            return
        try:
            logger.info("[Codegen:Generation] Running final validation repair for %s", self.config.operation_name)
            prompt_vars = {
                **input_data,
                "idx": feedback.index,
                "chunk": feedback.chunk,
                "result": state.code or initial_result,
                "validation_feedback": state.prompt_feedback(final_repair=True),
            }
            response = await self._invoke_generation_chain(
                chain, prompt_vars, context="final validation repair", run_name_suffix=":ValidationRepair"
            )
            candidate = await self._evaluate_response(response, *state.known_artifacts())
            if candidate is None:
                logger.warning(
                    "[Codegen:Generation] Final validation repair returned empty output; keeping previous code"
                )
                await append_job_error(
                    job_id, f"{self.config.logger_prefix} Final validation repair returned empty output"
                )
                return
            await self._record_candidate(
                state,
                candidate,
                chunk=feedback.chunk,
                index=feedback.index,
                context="final validation repair",
                job_id=job_id,
            )
            if state.pending is not None:
                logger.warning(
                    "[Codegen:Generation] Final validation repair left %d error(s) and %d warning(s); no further repair pass",
                    len(candidate.validation.errors),
                    len(candidate.validation.warnings),
                )
        except JobClaimLostError:
            raise
        except Exception as exc:
            raise_if_llm_unavailable(exc, context="repairing generated connector code")
            logger.exception("[Codegen:Generation] Final validation repair failed")
            await append_job_error(job_id, f"{self.config.logger_prefix} Final validation repair failed: {exc}")

    async def _load_documentation_items(self, session_id: UUID) -> List[Dict[str, Any]]:
        """Load documentation items from documentation_items table."""
        async with async_session_maker() as db:
            repo = DocumentationRepository(db)
            doc_items = await repo.get_documentation_items_by_session(session_id)
            return doc_items or []

    @staticmethod
    def _has_relevant_conndev_contracts(
        documentation_items: List[Dict[str, Any]],
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]],
    ) -> bool:
        """Return whether the empty LLM input was caused by selected conndev contracts."""
        if not documentation_items:
            return False

        conndev_chunk_ids: set[str] = set()
        for item in documentation_items:
            if not is_conndev_documentation_item(item):
                continue
            chunk_id = item.get("chunkId")
            if isinstance(chunk_id, str):
                conndev_chunk_ids.add(chunk_id)
        if not conndev_chunk_ids:
            return False

        # Preserve context-only generation for sessions composed exclusively of
        # deterministic conndev contracts, including legacy or missing relevance.
        if all(is_conndev_documentation_item(item) for item in documentation_items):
            return True

        if relevant_chunk_pairs is None:
            return False

        return any((pair.get("chunk_id") or pair.get("chunkId")) in conndev_chunk_ids for pair in relevant_chunk_pairs)

    def _build_chunks(
        self,
        documentation_items: List[Dict[str, Any]],
        relevant_chunk_pairs: Optional[List[Dict[str, Any]]],
    ) -> tuple[List[str], List[Optional[str]], Dict[str, int], List[str]]:
        """Build LLM chunks while keeping deterministic conndev contracts out of codegen."""
        if not documentation_items:
            logger.warning("%s No documentation items available", self.config.logger_prefix)
            return [], [], {}, []

        llm_documentation_items, llm_relevant_chunk_pairs = ChunkProcessor.exclude_conndev_contracts(
            documentation_items,
            relevant_chunk_pairs,
        )

        if llm_relevant_chunk_pairs is not None:
            # Use selected chunks based on pairs
            return ChunkProcessor.build_chunks_from_pairs(
                llm_relevant_chunk_pairs,
                llm_documentation_items,
                self.config.logger_prefix,
            )

        # Use all documentation items directly
        chunks = [normalize_to_text(item.get("content", "")) for item in llm_documentation_items]
        provenance: List[Optional[str]] = [item.get("chunkId") for item in llm_documentation_items]
        logger.info("%s Using all %d pre-chunked documentation items", self.config.logger_prefix, len(chunks))
        return chunks, provenance, {}, []

    async def _initialize_progress(
        self,
        job_id: UUID,
        chunks: List[str],
        chunk_ids_included: List[str],
    ):
        """Initialize job progress tracking."""
        total_chunks = len(chunks)
        logger.info("%s Processing %d chunks", self.config.logger_prefix, total_chunks)

        # Use selected chunk-id count if available, otherwise use chunk count as fallback
        total_count = len(chunk_ids_included) if chunk_ids_included else total_chunks

        await update_job_progress(
            job_id,
            stage=JobStage.processing_chunks,
            total_processing=total_count,
            processing_completed=0,
            message="Processing chunks and try to extract relevant information",
        )

    def _build_llm_chain(self, total_chunks: int) -> Runnable[Dict[str, Any], str]:
        """Build the LangChain chain for LLM invocation."""
        llm = get_default_llm()
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.config.system_prompt + VALIDATION_FEEDBACK_SYSTEM_RULES),
                ("human", self.config.user_prompt + "\n{validation_feedback}"),
            ]
        )

        partial_vars: Dict[str, Any] = {"total": total_chunks}
        partial_vars.update(self.config.extra_prompt_vars)

        prompt = prompt.partial(**partial_vars)
        return make_basic_chain(prompt, llm, StrOutputParser())

    async def _process_chunks(
        self,
        chunks: List[str],
        provenance_chunk_ids: List[Optional[str]],
        per_chunk_counts: Dict[str, int],
        chunk_ids_included: List[str],
        input_data: Dict[str, str],
        chain: Runnable[Dict[str, Any], str],
        job_id: UUID,
        initial_result: str,
    ) -> GenerationState:
        """Return accepted output and the latest candidate needing validation feedback.

        Supplied repair code remains prompt context until a replacement is accepted.
        Keeping it separate lets the caller preserve it without running cleanup when
        the model produces no replacement.
        """
        state = GenerationState()
        total_chunks = len(chunks)
        current_chunk_id: Optional[str] = None
        current_group_chunks_remaining: int = 0

        for idx, chunk in enumerate(chunks, start=1):
            chunk_id = provenance_chunk_ids[idx - 1] if idx - 1 < len(provenance_chunk_ids) else None

            try:
                # Update current group when running in selected-chunk mode
                if per_chunk_counts and chunk_ids_included and isinstance(chunk_id, str):
                    if current_chunk_id != chunk_id:
                        total_for_chunk_group = per_chunk_counts.get(chunk_id, 0)
                        current_chunk_id = chunk_id
                        current_group_chunks_remaining = total_for_chunk_group

                # Log progress
                if chunk_id:
                    logger.info(
                        "[Codegen:Generation] LLM call %d/%d (chunk_id: %s)",
                        idx,
                        total_chunks,
                        chunk_id,
                    )
                else:
                    logger.info("[Codegen:Generation] LLM call %d/%d", idx, total_chunks)

                # Invoke LLM
                prompt_vars = {
                    **input_data,
                    "idx": idx,
                    "chunk": chunk,
                    "result": state.code or initial_result,
                    "validation_feedback": state.prompt_feedback(),
                }
                context = f"chunk {idx}/{total_chunks}"
                response = await self._invoke_generation_chain(chain, prompt_vars, context=context)
                candidate = await self._evaluate_response(response, *state.known_artifacts())

                if candidate is not None:
                    await self._record_candidate(
                        state, candidate, chunk=chunk, index=idx, context=context, job_id=job_id
                    )

            except JobClaimLostError:
                raise
            except Exception as exc:
                raise_if_llm_unavailable(exc, context="generating connector code")
                error_message = f"{self.config.logger_prefix} Failed to process chunk {idx}/{total_chunks}: {exc}"
                logger.exception("[Codegen:Generation] Failed to process chunk %d/%d", idx, total_chunks)
                await append_job_error(job_id, error_message)
                continue

            finally:
                active_exception = sys.exception()
                try:
                    # Handle progress tracking based on mode
                    if per_chunk_counts and chunk_ids_included and isinstance(chunk_id, str):
                        # Selected-chunk mode: increment when this group is complete
                        current_group_chunks_remaining = max(0, current_group_chunks_remaining - 1)
                        if current_group_chunks_remaining == 0:
                            await increment_processed_documents(job_id, delta=1)
                    else:
                        await increment_processed_documents(job_id, delta=1)
                except JobClaimLostError:
                    if active_exception is None:
                        raise
                    logger.warning(
                        "[Codegen:Generation] Job claim was lost while recording progress; preserving the active error: %s",
                        active_exception,
                    )

        return state


def endpoints_to_records(payload: EndpointsPayload) -> List[Dict[str, Any]]:
    """Convert endpoints payload to list of records."""
    if isinstance(payload, EndpointResponse):
        return [
            strip_relevant_documentation_refs(cast(Dict[str, Any], ep.model_dump())) for ep in (payload.endpoints or [])
        ]

    if isinstance(payload, Mapping):
        if "endpoints" in payload and isinstance(payload["endpoints"], list):
            return [
                strip_relevant_documentation_refs(cast(Mapping[str, Any], endpoint))
                for endpoint in payload["endpoints"]
                if isinstance(endpoint, Mapping)
            ]
        if all(k in payload for k in ("path", "method", "description")):
            return [strip_relevant_documentation_refs(payload)]
    return []
