# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from src.config import config
from src.core import readiness
from src.core.readiness import ComponentStatus, check_database, check_llm, check_readiness

API_KEY = "llm-secret-key"
MODEL = "served/model"


@pytest.fixture(autouse=True)
def llm_settings(monkeypatch):
    monkeypatch.setattr(config.llm, "openai_api_base", "https://llm.example/v1/")
    monkeypatch.setattr(config.llm, "openai_api_key", SecretStr(API_KEY))
    monkeypatch.setattr(config.llm, "model_name", MODEL)


def _serve(monkeypatch, handler) -> list[httpx.Request]:
    """Route the shared LLM client through ``handler`` and record the requests it receives."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    monkeypatch.setattr(readiness, "get_llm_http_client", lambda: client)
    return requests


def _models(*model_ids: str) -> httpx.Response:
    return httpx.Response(200, json={"object": "list", "data": [{"id": model_id} for model_id in model_ids]})


def _engine(execute: AsyncMock) -> MagicMock:
    engine = MagicMock()
    engine.connect.return_value.__aenter__.return_value.execute = execute
    return engine


@pytest.mark.asyncio
async def test_llm_ok_when_endpoint_lists_models(monkeypatch):
    requests = _serve(monkeypatch, lambda request: _models("other/model", MODEL))

    assert await check_llm() is ComponentStatus.ok
    assert len(requests) == 1
    assert str(requests[0].url) == "https://llm.example/v1/models"
    assert requests[0].headers["Authorization"] == f"Bearer {API_KEY}"


@pytest.mark.asyncio
async def test_llm_check_sends_no_authorization_without_key(monkeypatch):
    monkeypatch.setattr(config.llm, "openai_api_key", SecretStr(""))
    requests = _serve(monkeypatch, lambda request: _models(MODEL))

    assert await check_llm() is ComponentStatus.ok
    assert "Authorization" not in requests[0].headers


@pytest.mark.asyncio
async def test_llm_ok_when_configured_model_is_not_listed_under_its_name(monkeypatch):
    # LiteLLM lists its alias (gpt-oss-120b) but accepts the underlying name used in LLM__MODEL_NAME.
    _serve(monkeypatch, lambda request: _models("gpt-oss-120b"))

    assert await check_llm() is ComponentStatus.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 404, 500, 503])
async def test_llm_unavailable_on_error_status_without_leaking_key(monkeypatch, caplog, status_code):
    _serve(monkeypatch, lambda request: httpx.Response(status_code, json={"error": "nope"}))

    with caplog.at_level(logging.WARNING):
        assert await check_llm() is ComponentStatus.unavailable

    assert "[Core:Readiness] LLM check failed (HTTPStatusError)" in caplog.text
    assert API_KEY not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"models": [MODEL]}),
    ],
    ids=["not-json", "unexpected-shape"],
)
async def test_llm_unavailable_on_unexpected_response(monkeypatch, response):
    _serve(monkeypatch, lambda request: response)

    assert await check_llm() is ComponentStatus.unavailable


@pytest.mark.asyncio
async def test_llm_unavailable_when_endpoint_unreachable(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _serve(monkeypatch, refuse)

    assert await check_llm() is ComponentStatus.unavailable


@pytest.mark.asyncio
async def test_llm_unavailable_on_http_timeout(monkeypatch):
    def time_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    _serve(monkeypatch, time_out)

    assert await check_llm() is ComponentStatus.unavailable


@pytest.mark.asyncio
async def test_database_ok(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(readiness, "engine", _engine(execute))

    assert await check_database() is ComponentStatus.ok
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_unavailable_on_error(monkeypatch, caplog):
    monkeypatch.setattr(readiness, "engine", _engine(AsyncMock(side_effect=OSError("connection refused"))))

    with caplog.at_level(logging.WARNING):
        assert await check_database() is ComponentStatus.unavailable

    assert "[Core:Readiness] Database check failed (OSError): connection refused" in caplog.text


@pytest.mark.asyncio
async def test_database_unavailable_on_timeout(monkeypatch, caplog):
    async def hang(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(config.readiness, "check_timeout_seconds", 0.01)
    monkeypatch.setattr(readiness, "engine", _engine(AsyncMock(side_effect=hang)))

    with caplog.at_level(logging.WARNING):
        assert await check_database() is ComponentStatus.unavailable

    assert "[Core:Readiness] Database check timed out" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database", "llm", "expected"),
    [
        (ComponentStatus.ok, ComponentStatus.ok, ComponentStatus.ok),
        (ComponentStatus.ok, ComponentStatus.unavailable, ComponentStatus.unavailable),
        (ComponentStatus.unavailable, ComponentStatus.ok, ComponentStatus.unavailable),
        (ComponentStatus.unavailable, ComponentStatus.unavailable, ComponentStatus.unavailable),
    ],
)
async def test_readiness_is_ok_only_when_every_check_is_ok(monkeypatch, database, llm, expected):
    monkeypatch.setattr(readiness, "check_database", AsyncMock(return_value=database))
    monkeypatch.setattr(readiness, "check_llm", AsyncMock(return_value=llm))

    report = await check_readiness()

    assert report.status is expected
    assert report.checks.database is database
    assert report.checks.llm is llm
    assert report.version == config.app.version
