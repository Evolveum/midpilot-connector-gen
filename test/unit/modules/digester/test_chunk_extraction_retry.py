# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.config import config
from src.modules.digester.utils.chunk_extraction import extract_single_chunk


class _RetryResponse(BaseModel):
    items: list[str]


@pytest.mark.asyncio
async def test_extract_single_chunk_retries_transient_gateway_error(monkeypatch):
    monkeypatch.setattr(config.digester, "chunk_llm_retry_attempts", 2)
    monkeypatch.setattr(config.digester, "chunk_llm_retry_base_delay_seconds", 0)

    chain = AsyncMock()
    chain.ainvoke.side_effect = [
        RuntimeError("<html><h1>504 Gateway Time-out</h1></html>"),
        _RetryResponse(items=["User"]),
    ]

    with (
        patch("src.modules.digester.utils.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.utils.chunk_extraction.append_job_error") as append_job_error,
    ):
        items, has_relevant_data = await extract_single_chunk(
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
    monkeypatch.setattr(config.digester, "chunk_llm_retry_attempts", 3)
    chain = AsyncMock()
    chain.ainvoke.side_effect = ValueError("invalid prompt variable")

    with (
        patch("src.modules.digester.utils.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.utils.chunk_extraction.append_job_error", Mock()) as append_job_error,
    ):
        items, has_relevant_data = await extract_single_chunk(
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
