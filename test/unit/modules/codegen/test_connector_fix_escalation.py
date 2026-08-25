# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the one-round documentation escalation of the connector fix."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.errors import LLMUnavailableError
from src.modules.codegen.connector_fix import _load_escalation_documentation, fix_connector_code
from src.modules.codegen.enums import ArtifactKind
from src.modules.codegen.errors import ConnectorFixContextTooLargeError, ConnectorFixEscalationFailedError
from src.modules.codegen.schema import ConnectorFixLLMResponse
from src.modules.codegen.selection.artifact_catalog import ConnectorArtifact
from src.shared.enums import ApiType

UPDATE_CODE = 'objectClass("user") {\n    update {\n    }\n}'
FIXED_UPDATE = 'objectClass("user") {\n    update {\n        endpoint("/users")\n    }\n}'
CREATE_CODE = 'objectClass("user") {\n    create {\n    }\n}'
FIXED_CREATE = 'objectClass("user") {\n    create {\n        endpoint("/users")\n    }\n}'

SCRIPTS = [
    {"operationKey": "userCreate", "kind": "create", "objectClass": "user", "code": CREATE_CODE},
    {"operationKey": "userUpdate", "kind": "update", "objectClass": "user", "code": UPDATE_CODE},
]


def _llm(**kwargs) -> ConnectorFixLLMResponse:
    return ConnectorFixLLMResponse.model_validate(kwargs)


async def _run(pass_results, *, documentation="chunk text"):
    with (
        patch("src.modules.codegen.connector_fix.run_connector_fix_pass", new_callable=AsyncMock) as pass_mock,
        patch("src.modules.codegen.connector_fix.store_fixed_connector_scripts", new_callable=AsyncMock) as store,
        patch("src.modules.codegen.connector_fix.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.codegen.connector_fix.append_job_error", new_callable=AsyncMock) as errors,
        patch(
            "src.modules.codegen.connector_fix._load_escalation_documentation",
            new_callable=AsyncMock,
            return_value=documentation,
        ),
        patch(
            "src.modules.codegen.connector_fix.get_session_connection_target",
            new_callable=AsyncMock,
            return_value=("https://api.example.test", ""),
        ),
        patch("src.modules.codegen.connector_fix._load_dsl_documentation", return_value="docs"),
    ):
        pass_mock.side_effect = pass_results
        result = await fix_connector_code(
            scripts=SCRIPTS,
            midpoint_errors=["unsupported filter"],
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
        )
    return result, pass_mock, store, errors


@pytest.mark.asyncio
async def test_a_documentation_request_triggers_exactly_one_more_pass():
    first = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="How are filters encoded?")
    second = _llm(fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "filter syntax"}])

    result, pass_mock, _, _ = await _run([first, second])

    assert pass_mock.await_count == 2
    assert result.documentation_escalated is True
    assert result.documentation_query == "How are filters encoded?"
    assert [change.operation_key for change in result.changed_operations] == ["userUpdate"]
    escalation_kwargs = pass_mock.await_args_list[1].kwargs
    assert escalation_kwargs["documentation_chunks"] == "chunk text"
    assert escalation_kwargs["previous_attempt"] is first


@pytest.mark.asyncio
async def test_documentation_pass_preserves_first_pass_fixes_for_other_operations():
    first = _llm(
        fixedScripts=[{"operationKey": "userCreate", "code": FIXED_CREATE, "reason": "create fix"}],
        needsDocumentation=True,
        documentationQuery="How are updates encoded?",
    )
    second = _llm(fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "update fix"}])

    result, _, _, _ = await _run([first, second])

    assert [change.operation_key for change in result.changed_operations] == ["userCreate", "userUpdate"]


@pytest.mark.asyncio
async def test_documentation_pass_replaces_first_pass_fix_for_the_same_operation():
    first = _llm(
        fixedScripts=[{"operationKey": "UserUpdate", "code": FIXED_CREATE, "reason": "first"}],
        needsDocumentation=True,
        documentationQuery="Which endpoint is correct?",
        analysis="first analysis",
    )
    second = _llm(
        fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "second"}],
        analysis="second analysis",
    )

    result, _, _, _ = await _run([first, second])

    assert [change.reason for change in result.changed_operations] == ["second"]
    assert result.analysis == "second analysis"


@pytest.mark.asyncio
async def test_a_second_documentation_request_is_capped():
    first = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="more please")
    second = _llm(
        fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "best effort"}],
        needsDocumentation=True,
        documentationQuery="even more please",
    )

    result, pass_mock, _, _ = await _run([first, second])

    assert pass_mock.await_count == 2
    assert result.documentation_escalated is True


@pytest.mark.asyncio
async def test_a_documentation_request_without_a_query_does_not_escalate():
    only = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="   ")

    _, pass_mock, _, _ = await _run([only])

    assert pass_mock.await_count == 1


@pytest.mark.asyncio
async def test_no_available_documentation_keeps_the_first_pass_result():
    first = _llm(
        fixedScripts=[{"operationKey": "userUpdate", "code": FIXED_UPDATE, "reason": "guessed"}],
        needsDocumentation=True,
        documentationQuery="How are filters encoded?",
    )

    result, pass_mock, _, _ = await _run([first], documentation="")

    assert pass_mock.await_count == 1
    assert result.documentation_escalated is False
    assert [change.operation_key for change in result.changed_operations] == ["userUpdate"]


@pytest.mark.asyncio
async def test_the_first_pass_carries_no_documentation_context():
    _, pass_mock, _, _ = await _run([_llm(fixedScripts=[])])

    first_kwargs = pass_mock.await_args_list[0].kwargs
    assert first_kwargs["documentation_query"] is None
    assert first_kwargs["documentation_chunks"] == ""
    assert first_kwargs["previous_attempt"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_failure",
    [
        None,
        ConnectorFixContextTooLargeError(input_tokens=120_001, limit=120_000),
        LLMUnavailableError("running the documentation pass"),
    ],
    ids=["invalid-response", "token-budget", "llm-unavailable"],
)
async def test_a_failed_documentation_pass_keeps_valid_first_pass_repairs(second_failure):
    first = _llm(
        fixedScripts=[{"operationKey": "userCreate", "code": FIXED_CREATE, "reason": "create fix"}],
        needsDocumentation=True,
        documentationQuery="How are updates encoded?",
    )

    result, pass_mock, store, errors = await _run([first, second_failure])

    assert pass_mock.await_count == 2
    assert result.documentation_escalated is False
    assert [change.operation_key for change in result.changed_operations] == ["userCreate"]
    store.assert_awaited_once()
    fallback_error = errors.await_args_list[-1].args[1]
    assert "Documentation pass failed; using 1 valid first-pass repair(s)" in fallback_error


@pytest.mark.asyncio
async def test_a_failed_documentation_pass_without_a_first_pass_repair_fails_the_job():
    first = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="How are updates encoded?")
    failure = ConnectorFixContextTooLargeError(input_tokens=120_001, limit=120_000)

    with pytest.raises(ConnectorFixContextTooLargeError):
        await _run([first, failure])


@pytest.mark.asyncio
async def test_an_invalid_documentation_response_without_a_first_pass_repair_fails_the_job():
    first = _llm(fixedScripts=[], needsDocumentation=True, documentationQuery="How are updates encoded?")

    with pytest.raises(ConnectorFixEscalationFailedError):
        await _run([first, None])


@pytest.mark.asyncio
async def test_escalation_loads_every_relevant_chunk_by_id_and_excludes_conndev():
    session_id = uuid4()
    job_id = uuid4()
    first_chunk_id = uuid4()
    second_chunk_id = uuid4()
    conndev_chunk_id = uuid4()
    artifacts = [
        ConnectorArtifact(
            operation_key="userUpdate",
            kind=ArtifactKind.UPDATE,
            object_class="user",
            code=UPDATE_CODE,
        )
    ]
    pairs = [
        {"chunk_id": str(second_chunk_id)},
        {"chunk_id": str(first_chunk_id)},
        {"chunk_id": str(conndev_chunk_id)},
    ]
    documentation_items = [
        {"chunkId": str(first_chunk_id), "content": "first"},
        {"chunkId": str(second_chunk_id), "content": "second"},
        {
            "chunkId": str(conndev_chunk_id),
            "content": "contract",
            "metadata": {"content_type": "application/com.evolveum.conndev+json"},
        },
    ]
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=MagicMock())
    db_context.__aexit__ = AsyncMock(return_value=None)
    documentation_repo = MagicMock()
    documentation_repo.get_documentation_items_by_chunk_ids = AsyncMock(return_value=documentation_items)

    with (
        patch(
            "src.modules.codegen.connector_fix.collect_connector_relevant_chunks",
            new_callable=AsyncMock,
            return_value=pairs,
        ) as collect,
        patch("src.modules.codegen.connector_fix.async_session_maker", return_value=db_context),
        patch("src.modules.codegen.connector_fix.DocumentationRepository", return_value=documentation_repo),
    ):
        result = await _load_escalation_documentation(session_id, artifacts, job_id)

    collect.assert_awaited_once_with(session_id, ["user"])
    documentation_repo.get_documentation_items_by_chunk_ids.assert_awaited_once_with(
        session_id,
        [second_chunk_id, first_chunk_id, conndev_chunk_id],
    )
    assert result == "second\n\n---\n\nfirst"
