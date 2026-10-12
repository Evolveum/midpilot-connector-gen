# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.config import config
from src.core.errors import LLMUnavailableError
from src.modules.digester.extraction import llm_execution
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk, run_all_items_build_parallel
from src.modules.digester.extraction.llm_execution import invoke_llm


# PyCharm's monkeypatch inspection does not resolve pydantic model fields as attribute names
# (mypy resolves them correctly), so the field-name string arguments are suppressed here once.
# noinspection PyUnresolvedReferences
def _set_chunk_llm_retry(monkeypatch, *, attempts: int, base_delay_seconds: float | None = None) -> None:
    monkeypatch.setattr(config.digester, "chunk_llm_retry_attempts", attempts)
    if base_delay_seconds is not None:
        monkeypatch.setattr(config.digester, "chunk_llm_retry_base_delay_seconds", base_delay_seconds)


class _RetryResponse(BaseModel):
    items: list[str]


class _SequenceMarker(BaseModel):
    start_sequence: str
    end_sequence: str


class _SequenceItem(BaseModel):
    relevant_sequences: list[_SequenceMarker]


class _SequenceResponse(BaseModel):
    items: list[_SequenceItem]


@pytest.mark.asyncio
async def test_extract_single_chunk_retries_transient_gateway_error(monkeypatch):
    _set_chunk_llm_retry(monkeypatch, attempts=2, base_delay_seconds=0)

    chain = AsyncMock()
    chain.ainvoke.side_effect = [
        RuntimeError("<html><h1>504 Gateway Time-out</h1></html>"),
        _RetryResponse(items=["User"]),
    ]

    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.append_job_error") as append_job_error,
    ):
        items, has_relevant_data = await extract_single_chunk(
            run_name="Digester:TestExtraction",
            schema="User resource documentation",
            pydantic_model=_RetryResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda result: result.items,
            job_id=uuid4(),
            chunk_id=uuid4(),
            extraction_chain=chain,
        )

    assert items == ["User"]
    assert has_relevant_data is True
    assert chain.ainvoke.await_count == 2
    append_job_error.assert_not_called()


@pytest.mark.asyncio
async def test_extract_single_chunk_does_not_retry_non_transient_error(monkeypatch):
    _set_chunk_llm_retry(monkeypatch, attempts=3)
    chain = AsyncMock()
    chain.ainvoke.side_effect = ValueError("invalid prompt variable")

    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extraction.chunk_extraction.append_job_error",
            new_callable=AsyncMock,
        ) as append_job_error,
    ):
        items, has_relevant_data = await extract_single_chunk(
            run_name="Digester:TestExtraction",
            schema="User resource documentation",
            pydantic_model=_RetryResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda result: result.items,
            job_id=uuid4(),
            chunk_id=uuid4(),
            extraction_chain=chain,
        )

    assert items == []
    assert has_relevant_data is False
    assert chain.ainvoke.await_count == 1
    append_job_error.assert_called_once()


@pytest.mark.asyncio
async def test_extract_single_chunk_raises_when_llm_unreachable(monkeypatch):
    """A connection error surviving retries must fail the job, not be swallowed per-chunk."""
    _set_chunk_llm_retry(monkeypatch, attempts=2, base_delay_seconds=0)

    chain = AsyncMock()
    chain.ainvoke.side_effect = Exception("Connection error.")

    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extraction.chunk_extraction.append_job_error",
            new_callable=AsyncMock,
        ) as append_job_error,
    ):
        with pytest.raises(LLMUnavailableError):
            await extract_single_chunk(
                run_name="Digester:TestExtraction",
                schema="User resource documentation",
                pydantic_model=_RetryResponse,
                system_prompt="system",
                user_prompt="user",
                parse_fn=lambda result: result.items,
                job_id=uuid4(),
                chunk_id=uuid4(),
                extraction_chain=chain,
            )

    # Retried before giving up, and the outage was not buried as a non-fatal chunk error.
    assert chain.ainvoke.await_count == 2
    append_job_error.assert_not_called()


@pytest.mark.asyncio
async def test_extract_single_chunk_validated_sequences_keep_matched_text():
    chunk_id = uuid4()
    chain = Mock()
    chain.ainvoke = AsyncMock(
        return_value=_SequenceResponse(
            items=[
                _SequenceItem(
                    relevant_sequences=[
                        _SequenceMarker(
                            start_sequence="User object",
                            end_sequence="email string",
                        )
                    ]
                )
            ]
        )
    )

    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.append_job_error") as append_job_error,
        patch("src.modules.digester.extraction.chunk_extraction.pool.require_process_pool", return_value=None),
    ):
        items, has_relevant_data = await extract_single_chunk(
            run_name="Digester:TestExtraction",
            schema="Prefix. User object includes id string and email string. Suffix.",
            pydantic_model=_SequenceResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda result: result.items,
            job_id=uuid4(),
            chunk_id=chunk_id,
            enabled_sequence_checking=True,
            extraction_chain=chain,
        )

    assert has_relevant_data is True
    assert len(items) == 1
    sequence = items[0].relevant_sequences[0]
    assert sequence.chunk_id == str(chunk_id)
    assert sequence.text == "User object includes id string and email string"
    append_job_error.assert_not_called()


@pytest.mark.asyncio
async def test_run_all_items_build_parallel_reuses_one_chain():
    chain = object()

    with (
        patch(
            "src.modules.digester.extraction.chunk_extraction.build_structured_chain",
            Mock(return_value=chain),
        ) as build_chain,
        patch(
            "src.modules.digester.extraction.chunk_extraction.run_item_build_parallel",
            new_callable=AsyncMock,
            side_effect=["built-a", "built-b"],
        ) as run_item,
    ):
        results = await run_all_items_build_parallel(
            run_name="Digester:TestBuild",
            items=["a", "b"],
            pydantic_model=_RetryResponse,
            system_prompt="system",
            user_prompt="user",
            job_id=uuid4(),
            parse_fn=lambda result, item: result,
        )

    assert results == ["built-a", "built-b"]
    build_chain.assert_called_once_with("system", "user", _RetryResponse, user_role="human")
    assert run_item.await_count == 2
    assert all(await_args.args[1] is chain for await_args in run_item.await_args_list)


@pytest.mark.asyncio
async def test_retry_backoff_releases_digester_slot_and_preserves_trace_name(monkeypatch):
    _set_chunk_llm_retry(monkeypatch, attempts=2, base_delay_seconds=0.25)
    monkeypatch.setattr(config.digester, "max_concurrent_llm_calls", 1)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore", None)
    monkeypatch.setattr(llm_execution, "_digester_llm_semaphore_limit", None)
    backoff_started = asyncio.Event()
    resume_retry = asyncio.Event()

    async def backoff(delay):
        assert delay == 0.25
        backoff_started.set()
        await resume_retry.wait()

    chain = AsyncMock()
    chain.ainvoke.side_effect = [RuntimeError("504 Gateway Timeout"), _RetryResponse(items=["User"])]
    other_chain = AsyncMock()
    other_chain.ainvoke.return_value = "other"
    with (
        patch("src.core.llm.asyncio.sleep", side_effect=backoff),
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
    ):
        task = asyncio.create_task(
            extract_single_chunk(
                schema="doc",
                pydantic_model=_RetryResponse,
                system_prompt="system",
                user_prompt="user",
                parse_fn=lambda result: result.items,
                job_id=uuid4(),
                extraction_chain=chain,
                run_name="Digester:Auth",
            )
        )
        try:
            await asyncio.wait_for(backoff_started.wait(), timeout=1)
            assert await asyncio.wait_for(invoke_llm(other_chain, {}, run_name="Digester:Other"), timeout=1) == "other"
            resume_retry.set()
            assert await asyncio.wait_for(task, timeout=1) == (["User"], True)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert [call.kwargs["config"]["run_name"] for call in chain.ainvoke.await_args_list] == ["Digester:Auth"] * 2


@pytest.mark.asyncio
async def test_cancellation_during_retry_backoff_is_not_retried_or_recorded_as_failure(monkeypatch):
    _set_chunk_llm_retry(monkeypatch, attempts=3, base_delay_seconds=0.25)
    chain = AsyncMock()
    chain.ainvoke.side_effect = RuntimeError("504 Gateway Timeout")
    with (
        patch("src.core.llm.asyncio.sleep", side_effect=asyncio.CancelledError),
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.append_job_error", new_callable=AsyncMock) as errors,
    ):
        with pytest.raises(asyncio.CancelledError):
            await extract_single_chunk(
                schema="doc",
                pydantic_model=_RetryResponse,
                system_prompt="system",
                user_prompt="user",
                parse_fn=lambda result: result.items,
                job_id=uuid4(),
                extraction_chain=chain,
                run_name="Digester:Auth",
            )
    chain.ainvoke.assert_awaited_once()
    errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_chunk_result_preserves_job_error_label():
    chain = AsyncMock()
    chain.ainvoke.return_value = None
    job_id, chunk_id = uuid4(), uuid4()
    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.append_job_error", new_callable=AsyncMock) as errors,
    ):
        assert await extract_single_chunk(
            schema="doc",
            pydantic_model=_RetryResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda result: result.items,
            job_id=job_id,
            chunk_id=chunk_id,
            extraction_chain=chain,
            run_name="Digester:Auth",
        ) == ([], False)
    errors.assert_awaited_once_with(job_id, f"[Digester:Auth] Empty LLM response. (chunk_id: {chunk_id})")
