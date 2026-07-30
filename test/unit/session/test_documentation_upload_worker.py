# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.session.documentation_processing import (
    _persist_processed_documentation_batch,
    process_documentation_worker,
)
from src.session.documentation_upload import RawUploadedDocumentation, UploadedDocumentation
from src.session.schema import ProcessedDocumentationChunk


class _AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_upload_persistence_uses_configured_transaction_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    repository = MagicMock()
    repository.create_documentation_item = AsyncMock(return_value=uuid4())
    chunks = [
        ProcessedDocumentationChunk(index=index, text=f"chunk-{index}", summary="summary", metadata={})
        for index in range(3)
    ]
    monkeypatch.setattr(config.jobs, "documentation_write_batch_size", 2)

    with (
        patch("src.session.documentation_processing.async_session_maker", return_value=_AsyncSessionContext(db)),
        patch("src.session.documentation_processing.DocumentationRepository", return_value=repository),
    ):
        await _persist_processed_documentation_batch(
            session_id=uuid4(),
            doc_id=uuid4(),
            job_id=uuid4(),
            filename="docs.md",
            chunks=chunks,
        )

    assert repository.create_documentation_item.await_count == 3
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_process_documentation_worker_updates_progress_per_chunk_and_persists_in_chunk_order():
    raw_upload = RawUploadedDocumentation(
        data=b"raw",
        filename="docs.md",
        content_type="text/markdown",
        content_hash="hash",
    )
    uploaded = UploadedDocumentation(
        text="first\nsecond",
        filename="docs.md",
        content_type="text/markdown",
        metadata={"filename": "docs.md", "content_type": "text/markdown", "parser": "text"},
    )

    async def fake_llm_processed_chunk(prompts: tuple[str, str]) -> SimpleNamespace:
        if prompts[1] == "first":
            await asyncio.sleep(0.01)
        return SimpleNamespace(
            summary=f"summary {prompts[1]}",
            num_endpoints=0,
            tags=["docs"],
            category="other",
            different_app_name=False,
        )

    with (
        patch(
            "src.session.documentation_processing.parse_uploaded_documentation", new_callable=AsyncMock
        ) as mock_parse,
        patch("src.session.documentation_processing.chunk_uploaded_documentation") as mock_chunk,
        patch("src.session.documentation_processing.get_llm_chunk_process_prompt") as mock_prompt,
        patch("src.session.documentation_processing.get_llm_processed_chunk", side_effect=fake_llm_processed_chunk),
        patch(
            "src.session.documentation_processing.update_job_progress", new_callable=AsyncMock
        ) as mock_update_progress,
        patch(
            "src.session.documentation_processing.increment_processed_documents", new_callable=AsyncMock
        ) as mock_increment,
        patch(
            "src.session.documentation_processing._persist_processed_documentation_chunk",
            new_callable=AsyncMock,
        ) as mock_persist,
    ):
        mock_parse.return_value = uploaded
        mock_chunk.return_value = [("first", 1), ("second", 1)]
        mock_prompt.side_effect = lambda chunk, filename, app, app_version: ("system", chunk)

        result = await process_documentation_worker(
            session_id=uuid4(),
            raw_upload=raw_upload,
            doc_id=uuid4(),
            app="Example",
            app_version="1.0",
            job_id=uuid4(),
        )

    assert result["chunks_processed"] == 2
    assert mock_increment.await_count == 2

    progress_kwargs = [call.kwargs for call in mock_update_progress.await_args_list]
    assert any(
        kwargs.get("total_processing") == 2 and kwargs.get("processing_completed") == 0 for kwargs in progress_kwargs
    )

    persisted_indexes = [call.kwargs["chunk"].index for call in mock_persist.await_args_list]
    assert persisted_indexes == [0, 1]


@pytest.mark.asyncio
async def test_process_documentation_worker_skips_llm_for_conndev_export():
    raw_upload = RawUploadedDocumentation(
        data=b"raw",
        filename="conndev_ScimSchema_Device.json",
        content_type="application/com.evolveum.conndev+json",
        content_hash="hash",
    )
    uploaded = UploadedDocumentation(
        text='{"schemaContent":"{}","name":"Device"}',
        filename=raw_upload.filename,
        content_type=raw_upload.content_type,
        metadata={
            "filename": raw_upload.filename,
            "content_type": raw_upload.content_type,
            "parser": "json",
        },
        preserve_as_single_item=True,
    )

    with (
        patch(
            "src.session.documentation_processing.parse_uploaded_documentation",
            new_callable=AsyncMock,
            return_value=uploaded,
        ),
        patch(
            "src.session.documentation_processing.chunk_uploaded_documentation",
            return_value=[(uploaded.text, 10)],
        ),
        patch(
            "src.session.documentation_processing.get_llm_processed_chunk", new_callable=AsyncMock
        ) as process_with_llm,
        patch("src.session.documentation_processing.update_job_progress", new_callable=AsyncMock),
        patch("src.session.documentation_processing.increment_processed_documents", new_callable=AsyncMock),
        patch(
            "src.session.documentation_processing._persist_processed_documentation_chunk",
            new_callable=AsyncMock,
        ) as persist_chunk,
    ):
        result = await process_documentation_worker(
            session_id=uuid4(),
            raw_upload=raw_upload,
            doc_id=uuid4(),
            app="Example",
            app_version="1.0",
            job_id=uuid4(),
        )

    assert result["chunks_processed"] == 1
    process_with_llm.assert_not_awaited()
    persisted = persist_chunk.await_args.kwargs["chunk"]
    assert persisted.metadata["category"] == "spec_json"
    assert persisted.metadata["tags"] == ["scim", "schema", "conndev"]
