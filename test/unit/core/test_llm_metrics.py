# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, Generation, LLMResult

from src.core.llm import get_default_llm
from src.core.observability.llm_metrics import (
    LlmCallMetricsHandler,
    get_llm_metrics_handler,
    start_llm_usage_tracking,
    stop_llm_usage_tracking,
)


@pytest.fixture
def usage():
    tracked, token = start_llm_usage_tracking()
    yield tracked
    stop_llm_usage_tracking(token)


def _chat_result(input_tokens: int, output_tokens: int) -> LLMResult:
    message = AIMessage(
        content="answer",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output={"model_name": "test-model"})


def _completion_result(prompt_tokens: int, completion_tokens: int) -> LLMResult:
    return LLMResult(
        generations=[[Generation(text="answer")]],
        llm_output={
            "token_usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "model_name": "test-model",
        },
    )


def _run(handler: LlmCallMetricsHandler, result: LLMResult) -> None:
    run_id = uuid4()
    handler.on_chat_model_start({}, [], run_id=run_id)
    handler.on_llm_end(result, run_id=run_id)


def test_chat_model_token_usage_is_accumulated(usage):
    _run(LlmCallMetricsHandler(), _chat_result(120, 30))

    assert usage.calls == 1
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 30
    assert usage.total_tokens == 150


def test_legacy_completion_token_usage_is_accumulated(usage):
    _run(LlmCallMetricsHandler(), _completion_result(80, 20))

    assert (usage.prompt_tokens, usage.completion_tokens) == (80, 20)


def test_missing_token_usage_counts_the_call_without_tokens(usage):
    _run(LlmCallMetricsHandler(), LLMResult(generations=[[Generation(text="answer")]]))

    assert usage.calls == 1
    assert usage.total_tokens == 0


def test_concurrent_calls_accumulate_into_the_same_totals(usage):
    handler = LlmCallMetricsHandler()
    first, second = uuid4(), uuid4()

    handler.on_chat_model_start({}, [], run_id=first)
    handler.on_chat_model_start({}, [], run_id=second)
    handler.on_llm_end(_chat_result(10, 1), run_id=second)
    handler.on_llm_end(_chat_result(20, 2), run_id=first)

    assert usage.calls == 2
    assert usage.total_tokens == 33


def test_failed_call_is_counted_separately(usage):
    handler = LlmCallMetricsHandler()
    run_id = uuid4()

    handler.on_chat_model_start({}, [], run_id=run_id)
    handler.on_llm_error(RuntimeError("provider exploded"), run_id=run_id)

    assert (usage.calls, usage.failed_calls) == (0, 1)


def test_untracked_calls_do_not_fail():
    """LLM calls also happen outside a job, e.g. while handling a request."""
    _run(LlmCallMetricsHandler(), _chat_result(1, 1))


def test_tracker_bounds_the_runs_it_remembers():
    """A cancelled call never reports an end; its start must not accumulate forever."""
    handler = LlmCallMetricsHandler()

    for _ in range(5000):
        handler.on_chat_model_start({}, [], run_id=uuid4())

    assert len(handler._started_at) <= 4096


def test_every_chat_model_reports_its_metrics():
    with patch("src.core.llm.ChatOpenAI") as chat_openai:
        get_default_llm()

    assert get_llm_metrics_handler() in chat_openai.call_args.kwargs["callbacks"]


def test_usage_summary_mentions_calls_and_tokens(usage):
    _run(LlmCallMetricsHandler(), _chat_result(120, 30))

    summary = usage.describe(elapsed_seconds=10.0)
    assert "1 LLM call(s)" in summary
    assert "120 prompt + 30 completion tokens" in summary


def test_usage_summary_reports_overlap_rather_than_implying_wall_clock(usage):
    """Concurrent calls sum past the elapsed time; the summary must not read as a bug."""
    usage.calls = 86
    usage.total_seconds = 5646.9

    summary = usage.describe(elapsed_seconds=376.7)

    assert "5646.9s summed over calls" in summary
    assert "15.0 concurrent on average" in summary
    assert "65.7s per call" in summary


def test_usage_summary_without_calls_omits_the_derived_figures(usage):
    summary = usage.describe(elapsed_seconds=376.7)

    assert "0 LLM call(s)" in summary
    assert "concurrent on average" not in summary


def test_usage_summary_tolerates_a_zero_length_job(usage):
    _run(LlmCallMetricsHandler(), _chat_result(1, 1))

    assert "concurrent on average" not in usage.describe(elapsed_seconds=0.0)
