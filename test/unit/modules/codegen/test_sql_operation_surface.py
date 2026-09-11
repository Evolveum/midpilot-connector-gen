# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the SQL branch of codegen: conndev context handling and the native sql DSL."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import generation, orchestration
from src.modules.codegen.core.operations import SearchGenerator
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets
from src.modules.digester.errors import OperationSurfaceNotFoundError
from src.shared.enums import ApiType

_CONNDEV_SQL_EXPORT = {
    "chunkId": "conndev-chunk",
    "content": '{"sql":{"object":{"attributes":{"table":"m_user"}}},"uid":"m_user","name":"m_user"}',
    "metadata": {"content_type": "application/com.evolveum.conndev+json"},
}

_GENERATED_CODE = 'objectClass("m_user") {\n    search {\n        sql {\n            builtIn {\n                enabled true\n            }\n        }\n    }\n}\n'


def _sql_search_generator(*, context_only_for_conndev: bool) -> SearchGenerator:
    return SearchGenerator(
        object_class="m_user",
        intent=SearchIntent.ALL,
        docs_text="Search docs",
        declarative_docs_text="Search docs (declarative)",
        system_prompt="System {total}",
        user_prompt="{chunk}",
        protocol_label=ApiType.SQL.value,
        database_name="midpoint",
        context_only_for_conndev=context_only_for_conndev,
    )


async def _generate_from_conndev_only_session(generator: SearchGenerator) -> str:
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_GENERATED_CODE)

    with (
        patch.object(SearchGenerator, "_load_documentation_items", new=AsyncMock(return_value=[_CONNDEV_SQL_EXPORT])),
        patch.object(SearchGenerator, "_build_llm_chain", return_value=chain),
        patch.object(SearchGenerator, "_cleanup_generated_code", new=AsyncMock(side_effect=lambda code, job_id: code)),
        patch("src.modules.codegen.core.base.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
    ):
        return await generator.generate(
            session_id=uuid4(),
            relevant_chunk_pairs=[{"chunk_id": "conndev-chunk", "doc_id": "conndev-doc"}],
            job_id=uuid4(),
            attributes={"attributes": {"oid": {"type": "string", "table": "m_user", "column": "oid"}}},
            endpoints={"endpoints": [{"table": "m_user", "columns": [{"name": "oid"}]}]},
        )


@pytest.mark.asyncio
async def test_sql_generation_runs_context_only_pass_when_session_is_conndev_only():
    """
    The SQL operation surface is extracted deterministically, so a session made only of conndev
    exports still has everything the prompt needs. Without the context-only pass the generator
    would return the empty scaffold and no code would ever be produced for such a session.
    """
    code = await _generate_from_conndev_only_session(_sql_search_generator(context_only_for_conndev=True))

    assert "sql {" in code
    assert "builtIn" in code
    assert code != "search {\n}\n"


@pytest.mark.asyncio
async def test_generation_without_context_only_pass_falls_back_to_empty_scaffold():
    """Guards the contrast: the pass is what keeps a conndev-only session from producing nothing."""
    code = await _generate_from_conndev_only_session(_sql_search_generator(context_only_for_conndev=False))

    assert code == "search {\n}\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol", "expects_context_only"),
    [(ApiType.SQL, True), (ApiType.SCIM, True), (ApiType.REST, False)],
)
async def test_search_generation_enables_context_only_pass_per_protocol(protocol: ApiType, expects_context_only: bool):
    with (
        patch(
            "src.modules.codegen.generation.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("", "midpoint"),
        ),
        patch(
            "src.modules.codegen.generation._collect_relevant_chunks",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.modules.codegen.generation.SearchGenerator") as mock_generator_class,
    ):
        mock_generator_class.return_value.generate = AsyncMock(return_value="code")

        await generation.generate_search_code(
            attributes={"attributes": {}},
            endpoints={"endpoints": []},
            session_id=uuid4(),
            object_class="m_user",
            intent=SearchIntent.ALL,
            job_id=uuid4(),
            protocol=protocol,
        )

    kwargs = mock_generator_class.call_args.kwargs
    assert kwargs["context_only_for_conndev"] is expects_context_only
    assert kwargs["include_scim_context"] is (protocol is ApiType.SCIM)


@pytest.mark.parametrize("intent", list(SearchIntent))
def test_sql_search_prompts_require_the_native_sql_block(intent: SearchIntent):
    assets = get_search_operation_assets(ApiType.SQL, intent)

    assert "sql {{ builtIn {{" in assets.system_prompt
    assert "endpoint(...)" in assets.system_prompt
    assert "<extracted_attributes>" in assets.user_prompt
    # The table listing is gone; nothing may ask for it back.
    assert "<sql_tables>" not in assets.user_prompt
    assert "endpoints_json" not in assets.user_prompt
    # Iterative refinement across chunks needs the previous result in the prompt.
    assert "{result}" in assets.user_prompt


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_sql_operation_prompts_require_the_native_sql_block(operation: str):
    assets = get_operation_assets(operation, ApiType.SQL)

    assert f"{operation} {{{{ sql {{{{ builtIn {{{{ enabled true }}}}" in assets.system_prompt
    assert "<extracted_attributes>" in assets.user_prompt
    assert "<sql_tables>" not in assets.user_prompt
    assert "endpoints_json" not in assets.user_prompt
    assert "{result}" in assets.user_prompt


@pytest.mark.asyncio
async def test_sql_operation_is_scheduled_without_an_endpoint_surface():
    """
    A SQL session never runs endpoint extraction, so ``{oc}EndpointsOutput`` never exists.
    Scheduling must go through on attributes alone instead of raising OperationSurfaceNotFoundError.
    """
    repo = MagicMock()
    repo.db = MagicMock()
    repo.get_session_data = AsyncMock(
        side_effect=lambda _session_id, key: (
            {"attributes": {"oid": {"type": "string", "table": "m_user", "column": "oid"}}}
            if key.endswith("AttributesOutput")
            else None
        )
    )
    repo.update_session = AsyncMock()
    job_id = uuid4()

    with (
        patch(
            "src.modules.codegen.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
        patch(
            "src.modules.codegen.orchestration.schedule_coroutine_job",
            new_callable=AsyncMock,
            return_value=job_id,
        ) as mock_schedule,
        patch("src.modules.codegen.orchestration.persist_job_pointer", new_callable=AsyncMock),
    ):
        scheduled = await orchestration.schedule_operation_job(
            repo=repo,
            session_id=uuid4(),
            object_class="m_user",
            skip_cache=False,
            api_type=None,
            codegen_input=None,
            key_prefix="m_userSearch",
            job_type="codegen.getSearch",
            worker=AsyncMock(),
        )

    assert scheduled == job_id
    job_input = mock_schedule.call_args.kwargs["input_payload"]
    assert "endpoints" not in job_input
    assert "endpoints" not in mock_schedule.call_args.kwargs["worker_kwargs"]


@pytest.mark.asyncio
async def test_rest_operation_still_requires_an_endpoint_surface():
    """The guard stays in place for REST, where endpoints are the only way to reach the API."""
    repo = MagicMock()
    repo.db = MagicMock()
    repo.get_session_data = AsyncMock(
        side_effect=lambda _session_id, key: {"attributes": {}} if key.endswith("AttributesOutput") else None
    )

    with (
        patch(
            "src.modules.codegen.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.REST,
        ),
        pytest.raises(OperationSurfaceNotFoundError),
    ):
        await orchestration.schedule_operation_job(
            repo=repo,
            session_id=uuid4(),
            object_class="user",
            skip_cache=False,
            api_type=None,
            codegen_input=None,
            key_prefix="userSearch",
            job_type="codegen.getSearch",
            worker=AsyncMock(),
        )
