# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest

from src.documents import RetrievalMetadata, SavedDocumentation, SpecRetrievalMetadata
from src.documents.processing.processor import process_scraped_documentation
from src.documents.processing.schema import LlmChunkOutput


def _llm_output() -> LlmChunkOutput:
    return LlmChunkOutput(
        summary="summary",
        num_endpoints=0,
        tags=["docs"],
        category="overview",
        num_defined_object_classes=0,
    )


@pytest.mark.asyncio
async def test_scraped_documentation_is_chunked_off_the_event_loop():
    """Tokenizing a large scraped page must not stall coroutines sharing the loop."""
    documentation = SavedDocumentation(url="https://docs.example.com/api", content="page text")
    released = threading.Event()

    def blocking_split(text: str, *, max_tokens: int, overlap_ratio: float) -> list[tuple[str, int]]:
        # Only an independent coroutine can release this; on the event loop it would time out.
        assert released.wait(timeout=5), "chunking blocked the event loop"
        return [(text, 2)]

    async def independent_coroutine() -> None:
        await asyncio.sleep(0)
        released.set()

    with (
        patch(
            "src.documents.processing.processor.split_text_with_token_overlap",
            side_effect=blocking_split,
        ),
        patch(
            "src.documents.processing.processor.get_llm_processed_chunk",
            new_callable=AsyncMock,
            return_value=_llm_output(),
        ),
    ):
        (chunks, errors), _ = await asyncio.gather(
            process_scraped_documentation(
                documentation,
                asyncio.Semaphore(1),
                app="Example",
                app_version="1.0",
                chunk_length=100,
                source="scraper",
            ),
            independent_coroutine(),
        )

    assert errors == []
    assert [chunk.content for chunk in chunks] == ["page text"]


@pytest.mark.asyncio
async def test_scraped_documentation_chunking_failure_propagates_from_worker_thread():
    documentation = SavedDocumentation(url="https://docs.example.com/api", content="page text")

    with patch(
        "src.documents.processing.processor.split_text_with_token_overlap",
        side_effect=RuntimeError("tokenizer unavailable"),
    ):
        with pytest.raises(RuntimeError, match="tokenizer unavailable"):
            await process_scraped_documentation(
                documentation,
                asyncio.Semaphore(1),
                app="Example",
                app_version="1.0",
                chunk_length=100,
                source="scraper",
            )


@pytest.mark.asyncio
async def test_retrieval_metadata_is_stored_on_every_chunk_and_absent_without_it():
    spec = SpecRetrievalMetadata(format="openapi", title="Example", canonical_hash="abc")
    scraped = SavedDocumentation(
        url="https://docs.example.com/openapi.json",
        content="spec text",
        retrieval=RetrievalMetadata(starter=True, spec=spec),
    )
    without_retrieval = SavedDocumentation(url="https://docs.example.com/page", content="page text")

    with (
        patch(
            "src.documents.processing.processor.split_text_with_token_overlap",
            side_effect=lambda text, **_: [(text, 2), (text, 2)],
        ),
        patch(
            "src.documents.processing.processor.get_llm_processed_chunk",
            new_callable=AsyncMock,
            return_value=_llm_output(),
        ),
    ):
        chunks, _ = await process_scraped_documentation(
            scraped, asyncio.Semaphore(1), app="Example", app_version="1.0", chunk_length=100, source="scraper"
        )
        plain_chunks, _ = await process_scraped_documentation(
            without_retrieval,
            asyncio.Semaphore(1),
            app="Example",
            app_version="1.0",
            chunk_length=100,
            source="scraper",
        )

    expected = {
        "version": 1,
        "starter": True,
        "spec": {"format": "openapi", "title": "Example", "canonical_hash": "abc"},
    }
    assert [chunk.metadata["retrieval"] for chunk in chunks] == [expected, expected]
    assert RetrievalMetadata.from_metadata(chunks[0].metadata) == scraped.retrieval
    assert "retrieval" not in plain_chunks[0].metadata
    assert RetrievalMetadata.from_metadata(plain_chunks[0].metadata) is None
