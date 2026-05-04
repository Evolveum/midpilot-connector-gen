# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.digester.utils.concurrent_chunk_runner import run_chunks_concurrently


@pytest.mark.asyncio
async def test_run_chunks_concurrently_respects_configured_limit(monkeypatch):
    monkeypatch.setattr(config.digester, "max_concurrent_chunk_llm_calls", 2)
    active = 0
    max_active = 0

    async def extractor(content, job_id, chunk_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return [content], True

    chunk_items = [{"chunkId": str(uuid4()), "content": f"chunk-{idx}"} for idx in range(5)]

    with (
        patch("src.modules.digester.utils.concurrent_chunk_runner.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.utils.concurrent_chunk_runner.increment_processed_documents", new_callable=AsyncMock
        ),
    ):
        results = await run_chunks_concurrently(
            chunk_items=chunk_items,
            job_id=uuid4(),
            extractor=extractor,
            logger_scope="test",
        )

    assert len(results) == len(chunk_items)
    assert max_active == 2
