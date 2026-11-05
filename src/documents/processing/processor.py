# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from src.config import config
from src.documents import SavedDocumentation
from src.documents.chunking import split_api_description, split_single_item_schema, split_text_with_token_overlap
from src.documents.normalize import merge_object_class_tags, normalize_object_class_tag, object_class_tag_variants
from src.documents.processing.llms import get_llm_processed_chunk
from src.documents.processing.prompts import get_llm_chunk_process_prompt
from src.documents.processing.schema import ChunkProcessingError, LlmChunkOutput
from src.session.schema import DocumentationItem

logger = logging.getLogger(__name__)

# Whitespace, quotes, markdown emphasis and code marks an LLM tends to change when it quotes a chunk.
_QUOTE_NOISE = re.compile(r"[\s*`\\\"']+")
# A schema name, path or other identifier inside the evidence, such as ``UserModel`` or ``/api/v3/users/{id}``.
_EVIDENCE_IDENTIFIER = re.compile(r"[\w./{}$:-]{3,}")


@dataclass(frozen=True)
class _DocumentChunk:
    """A chunk of a document and what the document structure already tells about it."""

    text: str
    token_count: int
    category: str | None = None
    num_endpoints: int | None = None
    object_classes: tuple[str, ...] = ()


def tag_object_classes(
    data: LlmChunkOutput, content: str, *, document: str, structural_classes: Sequence[str] = ()
) -> LlmChunkOutput:
    """Return ``data`` whose tags also name the object classes the chunk demonstrably documents.

    A class the LLM names is kept only when its evidence names the class and appears in the chunk, as a whole
    or through an identifier in it that contains the class name, so a guessed class never becomes a tag the
    digester selects chunks by. ``structural_classes``, read from the structure of an API description, need no
    evidence. ``document`` (URL or filename) is for logging.
    """
    comparable_content = _QUOTE_NOISE.sub("", content.lower())
    verified: list[str] = []
    dropped: list[str] = []
    for mention in data.object_classes:
        if not mention.name.strip():
            continue
        if _is_evidenced(mention.name, mention.evidence, comparable_content):
            verified.append(mention.name)
        else:
            dropped.append(mention.name)
    if dropped:
        logger.info(
            "[Documents:Process] Dropped %s object classes without verifiable evidence in a chunk of %s: %s",
            len(dropped),
            document,
            ", ".join(dropped),
        )
    return data.model_copy(update={"tags": merge_object_class_tags(data.tags, [*structural_classes, *verified])})


def _is_evidenced(name: str, evidence: str, comparable_content: str) -> bool:
    """Whether the evidence, or an identifier in it that contains the class name, appears in the chunk.

    LLMs often shorten a quote or wrap a schema name in their own words (``"UserModel" schema definition``),
    so a single identifier that names the class is enough.
    """
    variants = object_class_tag_variants(name)

    def names_class(text: str) -> bool:
        return any(variant in normalize_object_class_tag(text) for variant in variants)

    quote = _QUOTE_NOISE.sub("", evidence.strip(" .…").lower())
    if quote and quote in comparable_content and names_class(evidence):
        return True
    return any(
        names_class(identifier) and identifier.lower() in comparable_content
        for identifier in (match.strip(".:") for match in _EVIDENCE_IDENTIFIER.findall(evidence))
    )


def _split_documentation(documentation: SavedDocumentation, chunk_length: int) -> list[_DocumentChunk]:
    """Split a recognized API description by its structure and any other document by tokens. CPU-bound."""
    content = documentation.content
    spec = documentation.retrieval.spec if documentation.retrieval else None
    if content and spec is not None:
        if spec.format in ("openapi", "swagger"):
            split = split_api_description(content, max_tokens=chunk_length)
            if split is not None:
                category = f"spec_{split.syntax}"
                return [
                    _DocumentChunk(chunk.text, chunk.token_count, category, chunk.operation_count, chunk.object_classes)
                    for chunk in split.chunks
                ]
            logger.warning(
                "[Documents:Process] %s is not readable as %s; splitting it by tokens", documentation.url, spec.format
            )
        else:
            return [
                _DocumentChunk(text, token_count)
                for text, token_count in split_single_item_schema(
                    content, parser="json", filename=documentation.url, max_tokens=chunk_length
                )
            ]
    return [
        _DocumentChunk(text, token_count)
        for text, token_count in split_text_with_token_overlap(content, max_tokens=chunk_length, overlap_ratio=0.05)
    ]


def _with_structural_facts(data: LlmChunkOutput, chunk: _DocumentChunk) -> LlmChunkOutput:
    """Replace what the LLM estimated by what the document structure states: the category and endpoint count."""
    updates: Dict[str, Any] = {}
    if chunk.category in config.scrape_and_process.chunk_categories:
        updates["category"] = chunk.category
    if chunk.num_endpoints is not None:
        updates["num_endpoints"] = chunk.num_endpoints
    return data.model_copy(update=updates) if updates else data


def build_chunk_metadata(
    *,
    chunk_number: int,
    token_count: int,
    character_count: int,
    data: LlmChunkOutput,
    content_type: Optional[str] = None,
    filename: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the metadata dict persisted on a processed documentation chunk.

    Shared by the scraper and upload pipelines so both produce an identical
    metadata shape (notably ``chunk_number``, which was previously only set on
    uploads). ``content_type`` and ``filename`` are optional because the scraper
    carries a content type but no filename, while uploads carry both. ``extra``
    (e.g. upload parser metadata) is merged last.
    """
    metadata: Dict[str, Any] = {
        "chunk_number": chunk_number,
        "token_count": token_count,
        "character_count": character_count,
        "num_endpoints": data.num_endpoints,
        "tags": data.tags,
        "category": data.category,
    }
    if content_type is not None:
        metadata["content_type"] = content_type
    if filename is not None:
        metadata["filename"] = filename
    if extra:
        metadata.update(extra)
    return metadata


async def process_scraped_documentation(
    documentation: SavedDocumentation,
    semaphore: asyncio.Semaphore,
    app: str,
    app_version: str,
    chunk_length: int,
    source: str,
    scraper_job_id: Optional[UUID] = None,
) -> tuple[list[DocumentationItem], list[ChunkProcessingError]]:
    """
    Process a single scraped documentation:
    * generate chunks from the content
    * for each chunk, generate summary, tags and category
    * for each chunk, create DocumentationItem object

    Chunks are processed with per-chunk fault isolation: a failure on one chunk (e.g. a
    transient LLM connection error that survived retries) is recorded and skipped instead of
    aborting the whole documentation, so the remaining successful chunks are still returned.

    inputs:
        documentation: SavedDocumentation - the scraped documentation to process
        semaphore: asyncio.Semaphore - semaphore for limiting concurrency
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
    outputs:
        (chunks, errors): the successfully built DocumentationItem objects and any per-chunk failures
    """

    logger.debug("[Documents:Process] Processing documentation: %s", documentation.url)
    # Parsing and tokenization are CPU-bound; keep them off the event loop shared with the API.
    chunks = await asyncio.to_thread(_split_documentation, documentation, chunk_length)
    logger.debug("[Documents:Process] Generated %s chunks for documentation: %s", len(chunks), documentation.url)
    retrieval_metadata = (
        {"retrieval": documentation.retrieval.model_dump(exclude_none=True)} if documentation.retrieval else None
    )

    async def process_chunk(idx: int, chunk: _DocumentChunk) -> tuple[int, DocumentationItem]:
        prompts = get_llm_chunk_process_prompt(chunk.text, str(documentation.url), app, app_version)

        # A single shared semaphore is applied at chunk level
        async with semaphore:
            data = await get_llm_processed_chunk(prompts)
        data = tag_object_classes(data, chunk.text, document=documentation.url, structural_classes=chunk.object_classes)
        data = _with_structural_facts(data, chunk)

        documentation_chunk = DocumentationItem(
            doc_id=documentation.id,
            url=str(documentation.url),
            source=source,
            scrape_job_ids=[scraper_job_id] if scraper_job_id else [],
            summary=data.summary,
            metadata=build_chunk_metadata(
                chunk_number=idx,
                token_count=chunk.token_count,
                character_count=len(chunk.text),
                data=data,
                content_type=documentation.content_type,
                extra=retrieval_metadata,
            ),
            content=chunk.text,
        )
        return idx, documentation_chunk

    settled = await asyncio.gather(
        *[process_chunk(idx, chunk) for idx, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    processed_chunks: list[tuple[int, DocumentationItem]] = []
    errors: list[ChunkProcessingError] = []
    for idx, outcome in enumerate(settled):
        if isinstance(outcome, BaseException):
            logger.warning(
                "[Documents:Process] Skipping chunk %s of documentation %s after failure: %s",
                idx,
                documentation.url,
                outcome,
            )
            errors.append(
                ChunkProcessingError(url=str(documentation.url), chunk_index=idx, error=str(outcome) or repr(outcome))
            )
            continue
        processed_chunks.append(outcome)

    processed_chunks.sort(key=lambda item: item[0])
    documentation_chunks = [item[1] for item in processed_chunks]

    logger.info(
        "[Documents:Process] Completed processing documentation %s: %s chunks succeeded, %s failed",
        documentation.url,
        len(documentation_chunks),
        len(errors),
    )
    return documentation_chunks, errors


async def process_all_documentations(
    documentations: list[SavedDocumentation],
    app: str,
    app_version: str,
    source: str,
    *,
    semaphore: Optional[asyncio.Semaphore] = None,
    chunk_length: Optional[int] = None,
) -> tuple[list[DocumentationItem], list[ChunkProcessingError]]:
    """
    Process all scraped documentations concurrently with semaphore limiting.

    Per-chunk failures are isolated and returned alongside the successful chunks rather than
    aborting the batch, so a transient LLM error on one chunk cannot discard the whole run.

    inputs:
        documentations: list - list of Saveddocumentation objects to process
        app: str - application name
        app_version: str - application version
        chunk_length: int - maximum length of each chunk
        max_concurrent: int - maximum number of concurrent tasks
    outputs:
        (chunks, errors): all successfully built DocumentationItem objects and any per-chunk failures
    """
    effective_chunk_length = chunk_length or config.scrape_and_process.chunk_length
    local_semaphore = semaphore or asyncio.Semaphore(config.scrape_and_process.max_concurrent)

    results = await asyncio.gather(
        *[
            process_scraped_documentation(
                documentation, local_semaphore, app, app_version, effective_chunk_length, source
            )
            for documentation in documentations
        ]
    )

    all_chunks: list[DocumentationItem] = []
    all_errors: list[ChunkProcessingError] = []
    for documentation_chunks, documentation_errors in results:
        all_chunks.extend(documentation_chunks)
        all_errors.extend(documentation_errors)

    return all_chunks, all_errors
