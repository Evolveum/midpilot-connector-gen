# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

import asyncio
import json
import threading
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.config import config
from src.core.errors import LLMUnavailableError
from src.jobs.errors import JobClaimLostError
from src.modules.codegen.core.base import BaseGroovyGenerator
from src.modules.codegen.schema import CodegenRepairContext, OperationConfig
from src.modules.codegen.utils.connector_code_validation import inspect_connector_code

VALID = "objectClasses: {User: {search: {endpoints: [{path: users}]}}}"
INVALID = "objectClasses: {User: {references: []}}"
WARNING = "objectClasses: {User: {attributeResolvers: [{attribute: team}]}}"
FIXED = "objectClasses: {User: {search: {attributeResolvers: [{attribute: team}]}}}"


class _Generator(BaseGroovyGenerator):
    def __init__(self):
        super().__init__(OperationConfig("Search", "Framework {docs}", "{chunk}\n{result}", "", "[Codegen:Search]"))

    def prepare_input_data(self, **kwargs):
        return {"docs": "framework reference", "attributes_json": "extracted attributes"}

    def get_initial_result(self, **kwargs):
        return ""


@dataclass
class _Harness:
    generator: _Generator
    chain: AsyncMock
    cleanup: AsyncMock
    errors: AsyncMock
    progress: AsyncMock
    validator: MagicMock

    async def run(self, responses, *, chunks: int, repair_context=None):
        self.chain.ainvoke.side_effect = responses
        with patch.object(
            self.generator, "_build_chunks", return_value=([f"source-{i + 1}" for i in range(chunks)], [], {}, [])
        ):
            return await self.generator.generate(job_id=uuid4(), repair_context=repair_context)

    @property
    def calls(self):
        return [call.args[0] for call in self.chain.ainvoke.await_args_list]


@pytest.fixture
def harness():
    generator = _Generator()
    chain, cleanup = AsyncMock(), AsyncMock()
    cleanup.ainvoke.side_effect = lambda prompt_vars, **kwargs: next(iter(prompt_vars.values()))
    with (
        patch.object(generator, "_initialize_progress", new_callable=AsyncMock),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain", return_value=cleanup),
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock) as errors,
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock) as progress,
        patch("src.modules.codegen.core.base.inspect_connector_code", wraps=inspect_connector_code) as validator,
    ):
        yield _Harness(generator, chain, cleanup, errors, progress, validator)


def _feedback(call):
    return json.loads(
        call["validation_feedback"].split("<validation_feedback>\n")[1].split("\n</validation_feedback>")[0]
    )


@pytest.mark.asyncio
async def test_next_chunk_receives_rejected_draft_and_clears_resolved_error(harness):
    assert await harness.run([VALID, INVALID, FIXED, FIXED], chunks=4) == FIXED
    assert [call["result"] for call in harness.calls] == ["", VALID, VALID, FIXED]
    feedback = _feedback(harness.calls[2])
    assert feedback["artifact"] == "rejected_candidate"
    assert feedback["rejected_candidate"] == INVALID
    assert feedback["errors"] == [
        {"path": "objectClasses.User.references", "message": "Input should be a valid dictionary"}
    ]
    assert harness.calls[3]["validation_feedback"] == ""
    assert len(harness.calls) == harness.progress.await_count == 4
    harness.errors.assert_awaited_once()


@pytest.mark.asyncio
async def test_warning_is_advisory_and_does_not_duplicate_accepted_code(harness):
    assert await harness.run([WARNING, FIXED, FIXED], chunks=3) == FIXED
    feedback = _feedback(harness.calls[1])
    assert feedback["artifact"] == "result"
    assert feedback["errors"] == []
    assert feedback["warnings"][0]["path"] == "objectClasses.User.attributeResolvers"
    assert "rejected_candidate" not in feedback
    assert harness.calls[1]["result"] == WARNING
    assert harness.calls[2]["validation_feedback"] == ""
    harness.errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_diagnostics_are_replaced_not_accumulated(harness):
    second_invalid = "objectClasses: {User: {search: {endpoints: 42}}}"
    assert await harness.run([INVALID, second_invalid, VALID], chunks=2) == VALID
    final_feedback = _feedback(harness.calls[2])
    assert final_feedback["rejected_candidate"] == second_invalid
    assert "references" not in harness.calls[2]["validation_feedback"]
    assert final_feedback["errors"][0]["path"] == "objectClasses.User.search.endpoints"


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", [INVALID, WARNING])
async def test_final_repair_uses_source_context_and_does_not_increment_progress(harness, problem):
    assert await harness.run([VALID, problem, FIXED], chunks=2) == FIXED
    final_call = harness.calls[2]
    assert final_call["chunk"] == "source-2"
    assert final_call["idx"] == 2
    assert final_call["docs"] == "framework reference"
    assert final_call["attributes_json"] == "extracted attributes"
    assert "final local-validation repair pass" in final_call["validation_feedback"]
    assert harness.chain.ainvoke.await_args.kwargs["config"]["run_name"].endswith(":ValidationRepair")
    assert harness.progress.await_count == 2
    harness.cleanup.ainvoke.assert_awaited_once()
    assert harness.cleanup.ainvoke.await_args.kwargs["config"]["run_name"] == "Codegen:Search:Cleanup"
    assert harness.errors.await_count == (1 if problem == INVALID else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["", ValueError("bad response")])
async def test_empty_or_failed_chunk_retains_pending_draft_and_its_source(harness, interruption):
    assert await harness.run([INVALID, interruption, VALID], chunks=2) == VALID
    assert _feedback(harness.calls[2])["rejected_candidate"] == INVALID
    assert harness.calls[2]["chunk"] == "source-1"
    assert harness.calls[2]["idx"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_response", [INVALID, "", ValueError("bad repair response")])
async def test_failed_final_repair_preserves_last_accepted_artifact(harness, repair_response):
    assert await harness.run([VALID, INVALID, repair_response], chunks=2) == VALID
    assert len(harness.calls) == 3
    assert harness.errors.await_count == 2
    harness.cleanup.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [None, "broken("])
async def test_no_valid_repair_keeps_existing_empty_or_original_script_contract(harness, original):
    context = CodegenRepairContext(current_script=original, midpoint_errors=["runtime error"]) if original else None
    assert await harness.run([INVALID, INVALID], chunks=1, repair_context=context) == (original or "")
    assert harness.errors.await_count == 3
    harness.cleanup.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_unchanged_warnings_are_accepted_after_one_repair_and_logged_once(harness, caplog):
    assert await harness.run([WARNING, WARNING, WARNING], chunks=2) == WARNING
    assert len(harness.calls) == 3
    assert harness.calls[2]["chunk"] == "source-1"
    harness.validator.assert_called_once_with(WARNING)
    assert caplog.text.count("Unrecognized YAML option") == 1
    assert "no further repair pass" in caplog.text
    harness.errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_result_adds_no_repair_and_validation_runs_off_event_loop(harness):
    main_thread = threading.get_ident()

    def inspect_in_worker(code):
        assert threading.get_ident() != main_thread
        return inspect_connector_code(code)

    harness.validator.side_effect = inspect_in_worker
    assert await harness.run([VALID, VALID], chunks=2) == VALID
    assert len(harness.calls) == 2
    harness.validator.assert_called_once_with(VALID)
    harness.errors.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_rejects_invalid_output_after_successful_repair(harness):
    harness.cleanup.ainvoke.side_effect = [INVALID]
    assert await harness.run([INVALID, FIXED], chunks=1) == FIXED
    assert harness.errors.await_count == 2
    assert "Cleanup pass produced invalid output" in harness.errors.await_args.args[1]


@pytest.mark.asyncio
async def test_cleanup_outage_does_not_retry_and_preserves_accepted_code(harness):
    harness.cleanup.ainvoke.side_effect = Exception("Connection error.")
    with (
        patch.object(config.llm, "transient_retry_attempts", 3),
        patch.object(config.llm, "transient_retry_base_delay_seconds", 0),
    ):
        assert await harness.run([VALID], chunks=1) == VALID
    harness.chain.ainvoke.assert_awaited_once()
    harness.cleanup.ainvoke.assert_awaited_once()
    harness.errors.assert_awaited_once()
    assert harness.errors.await_args.args[1] == "[Codegen:Search] Cleanup pass failed: Connection error."


@pytest.mark.asyncio
async def test_groovy_syntax_feedback_and_repair_preserve_format(harness):
    broken = 'objectClass("User") { search {'
    fixed = 'objectClass("User") { search { endpoint("users") { emptyFilterSupported true } } }'
    assert await harness.run([broken, fixed], chunks=1) == fixed
    feedback = _feedback(harness.calls[1])
    assert feedback["rejected_candidate"] == broken
    assert feedback["errors"][0]["message"]
    assert harness.cleanup.ainvoke.await_args.args[0] == {"groovy_code": fixed}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.CancelledError(), JobClaimLostError("claim lost")])
async def test_final_repair_propagates_cancellation_and_lost_claim(harness, failure):
    with pytest.raises(type(failure)):
        await harness.run([INVALID, failure], chunks=1)
    assert len(harness.calls) == 2
    harness.errors.assert_awaited_once()
    harness.cleanup.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_repair_retries_transient_outage_then_fails_fast(harness):
    with (
        patch.object(config.llm, "transient_retry_attempts", 2),
        patch.object(config.llm, "transient_retry_base_delay_seconds", 0),
        pytest.raises(LLMUnavailableError),
    ):
        await harness.run([INVALID, Exception("Connection error."), Exception("Connection error.")], chunks=1)
    assert len(harness.calls) == 3
    harness.progress.assert_awaited_once()
    harness.errors.assert_awaited_once()


@pytest.mark.asyncio
async def test_lost_claim_while_recording_chunk_error_stops_generation(harness):
    harness.errors.side_effect = JobClaimLostError("claim lost")
    with pytest.raises(JobClaimLostError):
        await harness.run([INVALID, VALID], chunks=2)
    assert len(harness.calls) == 1
    harness.errors.assert_awaited_once()


def test_shared_chain_renders_feedback_as_data_without_template_interpolation():
    generator = _Generator()
    with (
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain") as make_chain,
    ):
        generator._build_llm_chain(2)
    prompt = make_chain.call_args.args[0]
    feedback = '<validation_feedback>{"rejected_candidate": "objectClass("User") {}"}</validation_feedback>'
    messages = prompt.format_messages(
        docs="framework reference", chunk="doc", result=VALID, validation_feedback=feedback
    )
    assert "Errors must be repaired even if" in messages[0].content
    assert "Warnings are advisory" in messages[0].content
    assert feedback in messages[1].content
