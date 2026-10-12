# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.digester.extraction import llm_execution
from src.modules.digester.extraction.llm_execution import invoke_chunk_chain, invoke_llm, run_chunks_concurrently
from src.modules.digester.extractors.rest.relations import _extract_from_chunk
from src.modules.digester.extractors.scim.attributes import extract_custom_scim_attributes
from src.modules.digester.schemas import RelationsResponse
from src.modules.digester.schemas.attributes import ExtractedAttributeResponseSCIM


# PyCharm's monkeypatch inspection does not resolve pydantic model fields as attribute names
# (mypy resolves them correctly), so the field-name string argument is suppressed here once.
# noinspection PyUnresolvedReferences
def _set_max_concurrent_llm_calls(monkeypatch, value: int) -> None:
    monkeypatch.setattr(config.digester, "max_concurrent_llm_calls", value)


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
    _set_max_concurrent_llm_calls(monkeypatch, 2)
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
        patch("src.modules.digester.extraction.llm_execution.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.llm_execution.increment_processed_documents", new_callable=AsyncMock),
    ):
        results = await run_chunks_concurrently(
            chunk_items=chunk_items,
            job_id=uuid4(),
            extractor=extractor,
        )

    assert len(results) == len(chunk_items)
    assert max_active == 2


@pytest.mark.asyncio
async def test_invoke_llm_respects_configured_limit(monkeypatch):
    _set_max_concurrent_llm_calls(monkeypatch, 3)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)

    chain = _TrackedChain()

    results = await asyncio.gather(*(invoke_llm(chain, idx, run_name="Digester:Test") for idx in range(10)))

    assert results == list(range(10))
    assert chain.max_active == 3


@pytest.mark.asyncio
async def test_nested_digester_llm_limit_does_not_deadlock(monkeypatch):
    _set_max_concurrent_llm_calls(monkeypatch, 1)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)
    chain = _TrackedChain()

    async def extractor(content, job_id, chunk_id):
        return [await invoke_llm(chain, content, run_name="Digester:Test")], True

    chunk_items = [{"chunkId": str(uuid4()), "content": f"chunk-{idx}"} for idx in range(3)]

    with (
        patch("src.modules.digester.extraction.llm_execution.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.llm_execution.increment_processed_documents", new_callable=AsyncMock),
    ):
        results = await asyncio.wait_for(
            run_chunks_concurrently(
                chunk_items=chunk_items,
                job_id=uuid4(),
                extractor=extractor,
            ),
            timeout=1,
        )

    assert [result for result, _, _ in results] == [[chunk["content"]] for chunk in chunk_items]
    assert chain.max_active == 1


@pytest.mark.asyncio
async def test_invoke_chunk_chain_names_the_langfuse_trace():
    chain = AsyncMock()
    chain.ainvoke = AsyncMock(return_value="result")

    await invoke_chunk_chain(chain, "chunk text", None, run_name="Digester:Relations")

    config = chain.ainvoke.await_args.kwargs["config"]
    assert config["run_name"] == "Digester:Relations"
    assert config["callbacks"]


@pytest.mark.asyncio
async def test_extractors_name_the_actual_chain_invocations():
    relations_chain = AsyncMock()
    relations_chain.ainvoke.return_value = RelationsResponse()
    scim_chain = AsyncMock()
    scim_chain.ainvoke.return_value = ExtractedAttributeResponseSCIM()

    assert await _extract_from_chunk(relations_chain, 0, "doc", uuid4()) == []
    assert await extract_custom_scim_attributes(scim_chain, "doc", "User") == {}

    relations_chain.ainvoke.assert_awaited_once()
    scim_chain.ainvoke.assert_awaited_once()
    assert relations_chain.ainvoke.await_args.kwargs["config"]["run_name"] == "Digester:Relations"
    assert scim_chain.ainvoke.await_args.kwargs["config"]["run_name"] == "Digester:SCIM:Attributes"


@pytest.mark.asyncio
async def test_blank_name_fails_before_chain_invocation():
    chain = AsyncMock()
    with pytest.raises(ValueError, match="run_name must not be blank"):
        await invoke_llm(chain, {}, run_name=" ")
    chain.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelling_invocation_releases_concurrency_slot(monkeypatch):
    _set_max_concurrent_llm_calls(monkeypatch, 1)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)
    started = asyncio.Event()

    async def blocked_call(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    chain = AsyncMock()
    chain.ainvoke.side_effect = blocked_call
    task = asyncio.create_task(invoke_llm(chain, {}, run_name="Digester:Cancelled"))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    next_chain = AsyncMock()
    next_chain.ainvoke.return_value = "next"
    assert await asyncio.wait_for(invoke_llm(next_chain, {}, run_name="Digester:Next"), timeout=1) == "next"
