# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for the object-class connector fix endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.app import create_api
from src.config.codegen import CodegenSettings
from src.jobs import job_input_reference
from src.modules.codegen.errors import (
    ConnectorFixContextTooLargeError,
    InvalidConnectorScriptOverrideError,
    UnknownConnectorOperationError,
)
from src.modules.codegen.routes.fix import fix_connector
from src.modules.codegen.schema import ConnectorFixInput
from src.modules.digester.errors import (
    AttributesNotFoundError,
    ObjectClassesNotFoundError,
    ObjectClassNotFoundError,
)
from src.shared.enums import ApiType

CREATE_CODE = 'objectClass("user") {\n    create {\n    }\n}'
UPDATE_CODE = 'objectClass("user") {\n    update {\n    }\n}'
ATTRIBUTES = {"attributes": {"Username": {"type": "string", "scimAttribute": "userName"}}}
ENDPOINTS = {"endpoints": [{"path": "/Users", "method": "GET", "description": "List users"}]}


def test_fix_input_token_limit_defaults_to_120000():
    assert CodegenSettings().fix_max_input_tokens == 120_000


def _repo(
    stored: dict | None = None,
    object_classes: object = None,
    session_data: dict | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.update_session = AsyncMock()

    data = (
        {"userAttributesOutput": ATTRIBUTES, "userEndpointsOutput": ENDPOINTS} if session_data is None else session_data
    )

    async def get_session_data(_session_id, key):
        return data.get(key)

    repo.get_session_data = AsyncMock(side_effect=get_session_data)

    async def get_session_value(_session_id, key):
        if key == "objectClassesOutput":
            return {"objectClasses": [{"name": "user"}]} if object_classes is None else object_classes
        return None

    repo.get_session_value = AsyncMock(side_effect=get_session_value)
    repo.get_session_values = AsyncMock(
        return_value=stored
        if stored is not None
        else {"userCreateOutput": {"code": CREATE_CODE}, "userUpdateOutput": {"code": UPDATE_CODE}}
    )
    return repo


async def _post(repo, codegen_input: ConnectorFixInput, job_id=None, *, object_class: str = "user"):
    with (
        patch("src.modules.codegen.routes.fix.SessionRepository", return_value=repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as schedule,
        patch(
            "src.modules.codegen.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.REST,
        ),
    ):
        schedule.return_value = job_id or uuid4()
        response = await fix_connector(
            uuid4(),
            object_class=object_class,
            db=MagicMock(),
            codegen_input=codegen_input,
        )
    return response, schedule


@pytest.mark.asyncio
async def test_fix_schedules_an_uncached_job_carrying_the_object_class_scripts():
    job_id = uuid4()
    repo = _repo()

    response, schedule = await _post(
        repo, ConnectorFixInput.model_validate({"midpointErrors": ["No such method"]}), job_id
    )

    assert response.jobId == job_id
    _, kwargs = schedule.call_args
    assert kwargs["job_type"] == "codegen.fixConnector"
    assert kwargs["input_payload"]["objectClass"] == "user"
    assert kwargs["input_payload"]["midpointErrors"] == ["No such method"]
    assert [s["operationKey"] for s in kwargs["input_payload"]["scripts"]] == ["userCreate", "userUpdate"]
    assert kwargs["worker_kwargs"]["scripts"] == job_input_reference("scripts")
    assert kwargs["worker_kwargs"]["midpoint_errors"] == job_input_reference("midpointErrors")

    # The write-back lives in the worker, so a cache hit would skip it entirely.
    assert kwargs["input_payload"]["skipCache"] is True
    # Many {key}Output rows are published by the worker; the job contract carries only one.
    assert "session_result_key" not in kwargs


@pytest.mark.asyncio
async def test_fix_carries_the_extracted_attributes_and_optional_endpoints():
    repo = _repo()

    _, schedule = await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))

    _, kwargs = schedule.call_args
    assert kwargs["input_payload"]["attributes"] == ATTRIBUTES
    assert kwargs["input_payload"]["endpoints"] == ENDPOINTS
    assert kwargs["worker_kwargs"]["attributes"] == job_input_reference("attributes")
    assert kwargs["worker_kwargs"]["endpoints"] == job_input_reference("endpoints")


@pytest.mark.asyncio
async def test_fix_runs_without_an_endpoint_surface():
    """A SQL session has no {objectClass}EndpointsOutput; the fix must not fail for it."""
    repo = _repo(session_data={"userAttributesOutput": ATTRIBUTES})

    _, schedule = await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))

    _, kwargs = schedule.call_args
    assert "endpoints" not in kwargs["input_payload"]
    assert "endpoints" not in kwargs["worker_kwargs"]


@pytest.mark.asyncio
async def test_fix_without_extracted_attributes_fails_instead_of_guessing():
    repo = _repo(session_data={})

    with pytest.raises(AttributesNotFoundError):
        await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))


@pytest.mark.asyncio
async def test_fix_persists_the_request_without_duplicating_stored_script_bodies():
    repo = _repo()

    await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))

    stored = repo.update_session.call_args[0][1]
    assert "userConnectorFixJobId" in stored
    assert "connectorFixJobId" not in stored
    manifest = stored["userConnectorFixInput"]
    assert manifest["objectClass"] == "user"
    assert manifest["operationKeys"] == ["userCreate", "userUpdate"]
    assert manifest["midpointErrors"] == ["boom"]
    assert manifest["scripts"] == []
    assert manifest["overriddenOperationKeys"] == []
    assert CREATE_CODE not in str(manifest)


@pytest.mark.asyncio
async def test_supplied_scripts_replace_the_stored_ones_for_this_run():
    repo = _repo()
    edited = 'objectClass("user") {\n    update {\n        endpoint("/users")\n    }\n}'

    _, schedule = await _post(
        repo,
        ConnectorFixInput.model_validate(
            {"midpointErrors": ["boom"], "scripts": [{"operationKey": "userUpdate", "code": edited}]}
        ),
    )

    scripts = {s["operationKey"]: s["code"] for s in schedule.call_args[1]["input_payload"]["scripts"]}
    assert scripts["userUpdate"] == edited
    assert scripts["userCreate"] == CREATE_CODE
    persisted_input = repo.update_session.call_args[0][1]["userConnectorFixInput"]
    assert persisted_input["overriddenOperationKeys"] == ["userUpdate"]
    assert persisted_input["scripts"] == [{"operationKey": "userUpdate", "code": edited}]
    assert CREATE_CODE not in str(persisted_input)


@pytest.mark.asyncio
async def test_fix_loads_scripts_only_for_the_selected_object_class():
    repo = _repo(
        object_classes={"objectClasses": [{"name": "user"}, {"name": "group"}, {"name": "role"}]},
        stored={
            "userCreateOutput": {"code": CREATE_CODE},
            "userUpdateOutput": {"code": UPDATE_CODE},
            "groupCreateOutput": {"code": 'objectClass("group") { create { } }'},
            "roleCreateOutput": {"code": 'objectClass("role") { create { } }'},
        },
    )

    _, schedule = await _post(
        repo,
        ConnectorFixInput.model_validate({"midpointErrors": ["user search failed"]}),
        object_class="User",
    )

    scheduled_scripts = schedule.call_args.kwargs["input_payload"]["scripts"]
    assert [script["operationKey"] for script in scheduled_scripts] == ["userCreate", "userUpdate"]
    requested_keys = repo.get_session_values.await_args.args[1]
    assert requested_keys
    assert all(key.startswith("user") for key in requested_keys)


@pytest.mark.asyncio
async def test_a_script_for_an_unknown_operation_is_rejected():
    repo = _repo()

    with pytest.raises(UnknownConnectorOperationError) as exc_info:
        await _post(
            repo,
            ConnectorFixInput.model_validate(
                {"midpointErrors": ["boom"], "scripts": [{"operationKey": "nope", "code": CREATE_CODE}]}
            ),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_a_connector_above_the_input_budget_is_rejected_not_truncated():
    repo = _repo()

    with (
        patch("src.modules.codegen.orchestration.config") as mock_config,
        patch("src.modules.codegen.orchestration.count_tokens", return_value=11),
        pytest.raises(ConnectorFixContextTooLargeError) as exc_info,
    ):
        mock_config.codegen.fix_max_input_tokens = 10
        await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))

    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_a_session_without_object_classes_schedules_nothing():
    repo = _repo(object_classes={})

    with pytest.raises(ObjectClassesNotFoundError):
        await _post(repo, ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}))

    repo.update_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_selected_object_class_schedules_nothing():
    repo = _repo(object_classes={"objectClasses": [{"name": "user"}]})

    with pytest.raises(ObjectClassNotFoundError):
        await _post(
            repo,
            ConnectorFixInput.model_validate({"midpointErrors": ["boom"]}),
            object_class="group",
        )

    repo.update_session.assert_not_awaited()


def test_openapi_exposes_object_class_scoped_fix_paths_and_parameter():
    schema = create_api().openapi()
    path = "/api/v1/codegen/{session_id}/classes/{objectClass}/fix"

    assert path in schema["paths"]
    assert "/api/v1/codegen/{session_id}/fix" not in schema["paths"]
    for method in ("post", "get"):
        parameters = schema["paths"][path][method]["parameters"]
        object_class_parameter = next(parameter for parameter in parameters if parameter["name"] == "objectClass")
        assert object_class_parameter["in"] == "path"
        assert object_class_parameter["required"] is True


@pytest.mark.asyncio
async def test_errors_are_required():
    with pytest.raises(ValueError):
        ConnectorFixInput.model_validate({"midpointErrors": []})
    with pytest.raises(ValueError):
        ConnectorFixInput.model_validate({"midpointErrors": ["   "]})


@pytest.mark.asyncio
async def test_a_supplied_script_is_validated_before_the_job_is_created():
    repo = _repo()

    with pytest.raises(InvalidConnectorScriptOverrideError) as exc_info:
        await _post(
            repo,
            ConnectorFixInput.model_validate(
                {"midpointErrors": ["boom"], "scripts": [{"operationKey": "userUpdate", "code": "class {{{"}]}
            ),
        )

    assert exc_info.value.status_code == 422
    repo.update_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_override_parsing_is_offloaded_after_the_size_check():
    repo = _repo()
    edited = 'objectClass("user") { update { endpoint("/users") } }'

    async def run_offloaded(function, *args):
        return function(*args)

    with patch(
        "src.modules.codegen.orchestration.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=run_offloaded,
    ) as to_thread:
        await _post(
            repo,
            ConnectorFixInput.model_validate(
                {"midpointErrors": ["boom"], "scripts": [{"operationKey": "userUpdate", "code": edited}]}
            ),
        )

    assert to_thread.await_count == 3


@pytest.mark.asyncio
async def test_oversized_override_is_rejected_before_parser_or_database_reads():
    repo = _repo()
    codegen_input = ConnectorFixInput.model_validate(
        {"midpointErrors": ["boom"], "scripts": [{"operationKey": "userUpdate", "code": "x" * 11}]}
    )

    with (
        patch("src.modules.codegen.orchestration.config") as mock_config,
        patch("src.modules.codegen.orchestration.asyncio.to_thread", new_callable=AsyncMock) as to_thread,
        patch("src.modules.codegen.orchestration.count_tokens", return_value=11),
        pytest.raises(ConnectorFixContextTooLargeError),
    ):
        mock_config.codegen.fix_max_input_tokens = 10
        to_thread.side_effect = lambda function, *args: function(*args)
        await _post(repo, codegen_input)

    to_thread.assert_awaited_once()
    repo.get_session_value.assert_not_awaited()
