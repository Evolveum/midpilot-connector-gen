# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.session.session import process_documentation_worker
from src.common.session.utils.documentation_upload import RawUploadedDocumentation, UploadedDocumentation


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
        patch("src.common.session.session.parse_uploaded_documentation", new_callable=AsyncMock) as mock_parse,
        patch("src.common.session.session.chunk_uploaded_documentation") as mock_chunk,
        patch("src.common.session.session.get_llm_chunk_process_prompt") as mock_prompt,
        patch("src.common.session.session.get_llm_processed_chunk", side_effect=fake_llm_processed_chunk),
        patch("src.common.session.session.update_job_progress", new_callable=AsyncMock) as mock_update_progress,
        patch("src.common.session.session.increment_processed_documents", new_callable=AsyncMock) as mock_increment,
        patch(
            "src.common.session.session._persist_processed_documentation_chunk",
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
