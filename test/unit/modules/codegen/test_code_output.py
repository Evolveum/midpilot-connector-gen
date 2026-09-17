# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Format reporting at the worker and persistence boundaries."""

import threading
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import generation
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.persistence import store_fixed_connector_scripts
from src.modules.codegen.schema import CodegenRepairContext
from src.modules.codegen.utils.code_output import build_connector_code_output
from src.modules.codegen.utils.connector_code_validation import detect_connector_code_format
from src.modules.digester.schemas import RelationsResponse
from src.shared.enums import ApiType


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_format"),
    [
        ("", None),
        (" \n\t", None),
        ("{}", "YAML"),
        ('objectClass("user") {}\n', "GROOVY"),
        (
            "objectClasses:\n  user:\n    search:\n      custom:\n        implementation: |\n          return []\n",
            "YAML",
        ),
    ],
)
async def test_output_preserves_code_and_classifies_off_event_loop(code, expected_format):
    event_loop_thread = threading.get_ident()

    def detect(value):
        assert threading.get_ident() != event_loop_thread
        return detect_connector_code_format(value)

    with patch("src.modules.codegen.utils.code_output.detect_connector_code_format", side_effect=detect) as detector:
        result = await build_connector_code_output(code)

    assert result == {"format": expected_format, "code": code}
    assert detector.call_count == (1 if code.strip() else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "native_schema",
        "conn_id",
        "authorization",
        "search_all",
        "search_filter",
        "search_id",
        "create",
        "update",
        "delete",
        "relation",
    ],
)
@pytest.mark.parametrize(
    ("code", "expected_format"), [("{}", "YAML"), ('objectClass("user") {}', "GROOVY"), ("", None)]
)
async def test_every_generation_worker_reports_final_format(operation, code, expected_format):
    kwargs = {"job_id": uuid4()}
    if operation != "conn_id":
        kwargs.update(session_id=uuid4(), protocol=ApiType.REST)
    if operation in {"native_schema", "conn_id"}:
        kwargs.update(attributes_payload={}, object_class="user")
        model_target = "generate_groovy"
    elif operation == "authorization":
        kwargs.update(auth_payload={"auth": []})
        model_target = "AuthorizationGenerator.generate"
    elif operation == "relation":
        kwargs.update(relations=RelationsResponse(relations=[]), relation_name="membership")
        model_target = "RelationGenerator.generate"
    else:
        kwargs.update(attributes={}, object_class="user")
        if operation.startswith("search_"):
            kwargs["intent"] = SearchIntent(operation.removeprefix("search_"))
            operation = "search"
        model_target = f"{operation.title()}Generator.generate"

    with ExitStack() as stack:
        for dependency, value in (
            ("get_session_connection_target", ("", "")),
            ("get_session_base_api_url", ""),
            ("_collect_relevant_chunks", []),
            ("_collect_authorization_relevant_chunks", []),
            ("_collect_relation_object_class_pairs", []),
        ):
            stack.enter_context(
                patch(f"src.modules.codegen.generation.{dependency}", new=AsyncMock(return_value=value))
            )
        model = stack.enter_context(
            patch(f"src.modules.codegen.generation.{model_target}", new=AsyncMock(return_value=code))
        )
        result = await getattr(generation, f"generate_{operation}_code")(**kwargs)

    assert result == {"format": expected_format, "code": code}
    model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("final_code", ["{}", 'objectClass("user") {}'])
async def test_single_operation_repair_reports_retained_code_format(final_code):
    original = "{}"
    repair_context = CodegenRepairContext(currentScript=original, midpointErrors=["Reported error"])
    with patch("src.modules.codegen.generation.generate_groovy", new=AsyncMock(return_value=final_code)):
        result = await generation.generate_native_schema_code(
            {}, "user", session_id=uuid4(), job_id=uuid4(), protocol=ApiType.REST, repair_context=repair_context
        )
    assert result == {"format": "YAML" if final_code == original else "GROOVY", "code": final_code}


@pytest.mark.asyncio
async def test_fixed_outputs_persist_format_and_code_in_one_write():
    session_id = uuid4()
    updates = {"userUpdateOutput": await build_connector_code_output("{}")}
    db = AsyncMock()
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=False)
    repo = MagicMock()
    repo.update_session = AsyncMock()
    with (
        patch("src.modules.codegen.persistence.async_session_maker", return_value=db_context),
        patch("src.modules.codegen.persistence.SessionRepository", return_value=repo),
        patch("src.modules.codegen.persistence.get_current_execution", return_value=None),
    ):
        await store_fixed_connector_scripts(session_id, updates, job_id=uuid4())

    repo.update_session.assert_awaited_once_with(session_id, {"userUpdateOutput": {"format": "YAML", "code": "{}"}})
    db.commit.assert_awaited_once()
