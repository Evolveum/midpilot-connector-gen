# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen.core.base import BaseGroovyGenerator, OperationConfig
from src.modules.codegen.core.generate_groovy import generate_groovy
from src.modules.codegen.schema import CodegenRepairContext


class _DummyChain:
    def __init__(self, responses):
        self._responses = list(responses)

    async def ainvoke(self, *args, **kwargs):
        return self._responses.pop(0)


class _RecordingChain(_DummyChain):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls = []

    async def ainvoke(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return await super().ainvoke(*args, **kwargs)


@pytest.mark.asyncio
async def test_generate_groovy_returns_scaffold_when_validation_fails() -> None:
    with (
        patch("src.modules.codegen.core.generate_groovy.get_default_llm"),
        patch("src.modules.codegen.core.generate_groovy.make_basic_chain", return_value=_DummyChain(["bad code"])),
        patch("src.modules.codegen.core.generate_groovy.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.core.generate_groovy.append_job_error") as mock_append_job_error,
        patch(
            "src.modules.codegen.core.generate_groovy.validate_groovy_code",
            return_value="syntax error",
        ),
    ):
        result = await generate_groovy(
            records=[{"name": "uid"}],
            object_class="User",
            system_prompt="system",
            user_prompt="user",
            job_id=uuid4(),
            logger_prefix="NativeSchema",
        )

    assert result == 'objectClass("User") {}'
    mock_append_job_error.assert_called_once()


@dataclass
class _DummyGenerator(BaseGroovyGenerator):
    def __init__(self):
        super().__init__(
            OperationConfig(
                operation_name="Dummy",
                system_prompt="system",
                user_prompt="user",
                default_scaffold='objectClass("User") {}',
                logger_prefix="[Codegen:Dummy]",
            )
        )

    def prepare_input_data(self, **kwargs):
        return {}

    def get_initial_result(self, **kwargs):
        return 'objectClass("User") {}'


@pytest.mark.asyncio
async def test_base_generator_keeps_previous_result_when_chunk_validation_fails() -> None:
    generator = _DummyGenerator()
    chain = _DummyChain(['objectClass("User") { broken', 'objectClass("User") { search {} }'])
    validation_results = [
        "chunk syntax error",
        None,
    ]

    with (
        patch("src.modules.codegen.core.base.append_job_error") as mock_append_job_error,
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.validate_groovy_code", side_effect=validation_results),
    ):
        result = await generator._process_chunks(
            chunks=["chunk-1", "chunk-2"],
            provenance_chunk_ids=[None, None],
            per_chunk_counts={},
            chunk_ids_included=[],
            input_data={},
            chain=chain,
            job_id=uuid4(),
            initial_result='objectClass("User") {}',
        )

    assert result == 'objectClass("User") { search {} }'
    mock_append_job_error.assert_called_once()


@pytest.mark.asyncio
async def test_base_generator_runs_repair_pass_without_documentation_chunks() -> None:
    generator = _DummyGenerator()
    repaired_code = 'objectClass("User") { search { } }'
    chain = _RecordingChain([repaired_code])

    with (
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain", return_value=chain),
        patch("src.modules.codegen.core.base.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.validate_groovy_code", return_value=None),
        patch.object(generator, "_cleanup_generated_code", new_callable=AsyncMock, return_value=repaired_code),
    ):
        result = await generator.generate(
            job_id=uuid4(),
            repair_context=CodegenRepairContext(
                currentScript='objectClass("User") { broken',
                midpointErrors=["Missing method: request.pathParameter(...)"],
            ),
        )

    assert result == repaired_code
    prompt_vars = chain.calls[0][0][0]
    assert prompt_vars["repair_system_suffix"] != ""
    assert prompt_vars["repair_user_suffix"] != ""
    assert prompt_vars["result"] == 'objectClass("User") { broken'


@pytest.mark.asyncio
async def test_base_generator_cleanup_returns_cleaned_code_when_valid() -> None:
    generator = _DummyGenerator()
    original_code = 'objectClass("User") { search { supportedFilter("id") { // TODO: map id\n } } }'
    cleaned_code = 'objectClass("User") { search { } }'

    with (
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain", return_value=_DummyChain([cleaned_code])),
        patch("src.modules.codegen.core.base.validate_groovy_code", return_value=None),
    ):
        result = await generator._cleanup_generated_code(code=original_code, job_id=uuid4())

    assert result == cleaned_code


@pytest.mark.asyncio
async def test_base_generator_cleanup_keeps_original_when_invalid() -> None:
    generator = _DummyGenerator()
    original_code = 'objectClass("User") { search { endpoint("users") { } } }'

    with (
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch(
            "src.modules.codegen.core.base.make_basic_chain",
            return_value=_DummyChain(['objectClass("User") { search { broken']),
        ),
        patch("src.modules.codegen.core.base.validate_groovy_code", return_value="syntax error"),
        patch("src.modules.codegen.core.base.append_job_error") as mock_append_job_error,
    ):
        result = await generator._cleanup_generated_code(code=original_code, job_id=uuid4())

    assert result == original_code
    mock_append_job_error.assert_called_once()
