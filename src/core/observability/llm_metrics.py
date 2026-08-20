# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Latency and token accounting for every LLM call.

LLM calls dominate both the latency and the cost of this service, so they are the
one thing that must never be invisible. The handler here is attached centrally in
:func:`src.core.llm.get_default_llm`, which is the only place a chat model is
constructed - no call site has to opt in, and no call path can be forgotten.

Two levels of detail are produced:

* per call - at ``DEBUG``, for when a single call has to be inspected;
* per job - an aggregate the job runner reports when the job ends, so the cost
  and the LLM share of a job's wall clock are visible at ``INFO``.
"""

import logging
import time
from collections import OrderedDict
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


_MAX_TRACKED_RUNS = 4096
_UNKNOWN_MODEL = "unknown"


@dataclass
class LlmUsage:
    """Mutable per-job totals accumulated across concurrent LLM calls."""

    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_seconds: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def describe(self, elapsed_seconds: float) -> str:
        """Render the totals for a single log line.

        ``total_seconds`` sums calls that overlap in time, so for any fan-out it
        legitimately exceeds the job's wall clock. Reporting it against the elapsed
        time turns that surprise into the useful reading: how many LLM calls the job
        kept in flight on average, which is what a provider-side queue shows up in.
        """
        summary = (
            f"{self.calls} LLM call(s), {self.failed_calls} failed, "
            f"{self.prompt_tokens} prompt + {self.completion_tokens} completion tokens, "
            f"{self.total_seconds:.1f}s summed over calls"
        )
        if not self.calls or elapsed_seconds <= 0:
            return summary

        concurrency = self.total_seconds / elapsed_seconds
        seconds_per_call = self.total_seconds / self.calls
        return f"{summary}, {concurrency:.1f} concurrent on average, {seconds_per_call:.1f}s per call"


_current_usage: ContextVar[LlmUsage | None] = ContextVar("current_llm_usage", default=None)


def start_llm_usage_tracking() -> tuple[LlmUsage, Token[LlmUsage | None]]:
    """Begin accumulating LLM usage for the current execution."""
    usage = LlmUsage()
    return usage, _current_usage.set(usage)


def stop_llm_usage_tracking(token: Token[LlmUsage | None]) -> None:
    """Stop accumulating LLM usage for the current execution."""
    _current_usage.reset(token)


class LlmCallMetricsHandler(BaseCallbackHandler):
    """Record the duration and token usage of each LLM call.

    ``run_inline`` keeps the bookkeeping on the calling thread or event loop.
    The work is a dictionary update and at most one log record, so running it
    inline is cheaper than the thread hand-off LangChain would otherwise do, and
    it keeps the job execution context - and therefore the log correlation ids -
    intact.

    Only chat models are timed, which is every model this service builds. Should a
    plain completion model ever be added, its tokens are still counted; only its
    latency goes unrecorded, because it reports no start this handler observes.
    """

    run_inline = True

    def __init__(self) -> None:
        super().__init__()
        self._started_at: OrderedDict[UUID, float] = OrderedDict()

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._record_start(run_id)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        duration = self._take_duration(run_id)
        prompt_tokens, completion_tokens = _extract_token_usage(response)
        model = _extract_model_name(response)

        usage = _current_usage.get()
        if usage is not None:
            usage.calls += 1
            usage.prompt_tokens += prompt_tokens
            usage.completion_tokens += completion_tokens
            if duration is not None:
                usage.total_seconds += duration

        if duration is None:
            # Without a start we cannot report the latency, but the tokens still count.
            logger.debug(
                "LLM call finished on %s: %s prompt + %s completion tokens", model, prompt_tokens, completion_tokens
            )
            return

        logger.debug(
            "LLM call on %s took %.1fs: %s prompt + %s completion tokens",
            model,
            duration,
            prompt_tokens,
            completion_tokens,
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        duration = self._take_duration(run_id)

        usage = _current_usage.get()
        if usage is not None:
            usage.failed_calls += 1
            if duration is not None:
                usage.total_seconds += duration

        logger.warning("LLM call failed after %.1fs: %s", duration if duration is not None else float("nan"), error)

    def _record_start(self, run_id: UUID) -> None:
        if len(self._started_at) >= _MAX_TRACKED_RUNS:
            self._started_at.popitem(last=False)
        self._started_at[run_id] = time.monotonic()

    def _take_duration(self, run_id: UUID) -> float | None:
        started = self._started_at.pop(run_id, None)
        return None if started is None else time.monotonic() - started


def _extract_token_usage(response: LLMResult) -> tuple[int, int]:
    """Read prompt and completion tokens from whichever shape the provider returned.

    Chat models report usage on the message, while the legacy completion shape puts
    it in ``llm_output``. Providers that report neither yield zeros rather than an
    error - a missing count must never break the call that produced it.
    """
    for generations in response.generations:
        for generation in generations:
            metadata = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if isinstance(metadata, Mapping):
                return _as_int(metadata.get("input_tokens")), _as_int(metadata.get("output_tokens"))

    token_usage = (response.llm_output or {}).get("token_usage")
    if isinstance(token_usage, Mapping):
        return _as_int(token_usage.get("prompt_tokens")), _as_int(token_usage.get("completion_tokens"))

    return 0, 0


def _extract_model_name(response: LLMResult) -> str:
    model = (response.llm_output or {}).get("model_name")
    return model if isinstance(model, str) and model else _UNKNOWN_MODEL


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


_metrics_handler = LlmCallMetricsHandler()


def get_llm_metrics_handler() -> LlmCallMetricsHandler:
    """Return the process-wide metrics handler attached to every chat model."""
    return _metrics_handler
