# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.digester.utils import llm_execution
from src.modules.digester.utils.llm_execution import invoke_digester_llm, run_chunks_concurrently


class _TrackedChain:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, input, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return input


@pytest.mark.asyncio
async def test_run_chunks_concurrently_respects_configured_limit(monkeypatch):
    monkeypatch.setattr(config.digester, "max_concurrent_llm_calls", 2)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)
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
        patch("src.modules.digester.utils.llm_execution.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.utils.llm_execution.increment_processed_documents", new_callable=AsyncMock),
    ):
        results = await run_chunks_concurrently(
            chunk_items=chunk_items,
            job_id=uuid4(),
            extractor=extractor,
            logger_scope="test",
        )

    assert len(results) == len(chunk_items)
    assert max_active == 2


@pytest.mark.asyncio
async def test_invoke_digester_llm_respects_configured_limit(monkeypatch):
    monkeypatch.setattr(config.digester, "max_concurrent_llm_calls", 3)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)

    chain = _TrackedChain()

    results = await asyncio.gather(*(invoke_digester_llm(chain, idx) for idx in range(10)))

    assert results == list(range(10))
    assert chain.max_active == 3


@pytest.mark.asyncio
async def test_nested_digester_llm_limit_does_not_deadlock(monkeypatch):
    monkeypatch.setattr(config.digester, "max_concurrent_llm_calls", 1)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)
    chain = _TrackedChain()

    async def extractor(content, job_id, chunk_id):
        return [await invoke_digester_llm(chain, content)], True

    chunk_items = [{"chunkId": str(uuid4()), "content": f"chunk-{idx}"} for idx in range(3)]

    with (
        patch("src.modules.digester.utils.llm_execution.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.utils.llm_execution.increment_processed_documents", new_callable=AsyncMock),
    ):
        results = await asyncio.wait_for(
            run_chunks_concurrently(
                chunk_items=chunk_items,
                job_id=uuid4(),
                extractor=extractor,
                logger_scope="test",
            ),
            timeout=1,
        )

    assert [result for result, _, _ in results] == [[chunk["content"]] for chunk in chunk_items]
    assert chain.max_active == 1
