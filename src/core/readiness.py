# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Dependency checks behind the readiness endpoint.

Each check is bounded by ``config.readiness.check_timeout_seconds`` and reports only
``ok`` or ``unavailable``. Failure details are logged and never returned to the caller.
"""

import asyncio
import logging
from enum import StrEnum
from typing import Awaitable, Callable

from pydantic import BaseModel
from sqlalchemy import text

from src.config import config
from src.core.db import engine
from src.core.llm import get_llm_http_client

logger = logging.getLogger(__name__)


class ComponentStatus(StrEnum):
    ok = "ok"
    unavailable = "unavailable"


class ReadinessChecks(BaseModel):
    """Per-dependency results of a readiness check."""

    database: ComponentStatus
    llm: ComponentStatus


class ReadinessReport(BaseModel):
    """Readiness of the service: ``ok`` only when every dependency check is ``ok``."""

    status: ComponentStatus
    version: str
    checks: ReadinessChecks


class _ServedModel(BaseModel):
    id: str


class _ServedModelList(BaseModel):
    """The part of an OpenAI-compatible ``GET /models`` response the LLM check relies on."""

    data: list[_ServedModel]


async def _probe_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _probe_llm() -> None:
    """List the models served by the LLM endpoint; no tokens are consumed.

    The response is validated as an OpenAI-compatible model list, but ``LLM__MODEL_NAME`` is not
    looked up in it: proxies such as LiteLLM list only their public aliases while also accepting the
    underlying provider model name, so a name comparison would report working setups as unavailable.
    """
    api_key = config.llm.openai_api_key.get_secret_value()
    response = await get_llm_http_client().get(
        f"{config.llm.openai_api_base.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        timeout=config.readiness.check_timeout_seconds,
    )
    response.raise_for_status()
    _ServedModelList.model_validate_json(response.content)


async def _run_check(name: str, probe: Callable[[], Awaitable[None]]) -> ComponentStatus:
    timeout = config.readiness.check_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            await probe()
    except TimeoutError:
        logger.warning("[Core:Readiness] %s check timed out after %ss", name, timeout)
        return ComponentStatus.unavailable
    except Exception as exc:
        logger.warning("[Core:Readiness] %s check failed (%s): %s", name, type(exc).__name__, exc)
        return ComponentStatus.unavailable
    return ComponentStatus.ok


async def check_database() -> ComponentStatus:
    return await _run_check("Database", _probe_database)


async def check_llm() -> ComponentStatus:
    return await _run_check("LLM", _probe_llm)


async def check_readiness() -> ReadinessReport:
    """Run all dependency checks concurrently and aggregate them into one report."""
    database, llm = await asyncio.gather(check_database(), check_llm())
    checks = ReadinessChecks(database=database, llm=llm)
    all_ok = database is ComponentStatus.ok and llm is ComponentStatus.ok
    return ReadinessReport(
        status=ComponentStatus.ok if all_ok else ComponentStatus.unavailable,
        version=config.app.version,
        checks=checks,
    )
