# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Codegen for an object class the digester found no attributes or endpoints for.

The digester no longer rejects such an object class: its extraction finishes with an empty
result. Codegen then still tries with what it knows - the chunks relevant to the object
class - and only when nothing at all is known returns its deterministic fallback.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import generation
from src.modules.codegen.core.base import BaseGroovyGenerator
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.orchestration import schedule_native_schema_job
from src.modules.codegen.repair import NO_ATTRIBUTES_EXTRACTED, NO_DOCUMENTED_BEHAVIOR
from src.modules.codegen.schema import CodegenRepairContext
from src.modules.codegen.selection.relevant_chunks import _collect_object_class_chunks
from src.modules.codegen.utils.operation_defaults import (
    operation_defaults_artifact,
    rest_operation_placeholder_artifact,
)
from src.modules.codegen.utils.prompt_records import has_extracted_attributes
from src.modules.digester.errors import SqlPhysicalSchemaNotFoundError
from src.modules.digester.schemas import AttributeResponse
from src.shared.enums import ApiType

EMPTY_ATTRIBUTES: dict[str, Any] = {"attributes": {}}
EMPTY_ENDPOINTS: dict[str, Any] = {"endpoints": []}
ATTRIBUTES: dict[str, Any] = {"attributes": {"name": {"type": "string"}}}
ENDPOINTS: dict[str, Any] = {"endpoints": [{"path": "/roles", "method": "GET", "description": "List roles"}]}
CLASS_CHUNKS = [{"chunk_id": "role-chunk", "doc_id": "role-doc"}]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (EMPTY_ATTRIBUTES, False),
        ({}, False),
        ({"attributes": {}, "scimContext": {"schemas": []}}, False),
        (AttributeResponse(attributes={}), False),
        (ATTRIBUTES, True),
        (AttributeResponse.model_validate(ATTRIBUTES), True),
    ],
)
def test_has_extracted_attributes(payload, expected):
    assert has_extracted_attributes(payload) is expected


@pytest.mark.asyncio
async def test_object_class_chunks_are_read_for_the_normalized_class_name():
    chunk_map = {
        "role": [{"docId": "role-doc", "chunkId": "role-chunk"}],
        "user": [{"docId": "user-doc", "chunkId": "user-chunk"}],
    }
    with patch(
        "src.modules.codegen.selection.relevant_chunks._load_object_class_chunk_map",
        new=AsyncMock(return_value=chunk_map),
    ):
        assert await _collect_object_class_chunks(uuid4(), "Role") == CLASS_CHUNKS


async def _chunk_pairs(
    *,
    attribute_and_endpoint_chunks,
    object_class_chunks,
    attributes=EMPTY_ATTRIBUTES,
    endpoints=None,
    preferred_endpoints=None,
):
    with (
        patch(
            "src.modules.codegen.generation._collect_relevant_chunks",
            new=AsyncMock(return_value=attribute_and_endpoint_chunks),
        ),
        patch(
            "src.modules.codegen.generation._collect_object_class_chunks",
            new=AsyncMock(return_value=object_class_chunks),
        ) as collect_class_chunks,
    ):
        pairs = await generation._operation_chunk_pairs(
            uuid4(),
            "role",
            "Search",
            attributes=attributes,
            endpoints=endpoints,
            preferred_endpoints=preferred_endpoints,
        )
    return pairs, collect_class_chunks


@pytest.mark.asyncio
async def test_attribute_and_endpoint_chunks_take_precedence():
    selected = [{"chunk_id": "attribute-chunk", "doc_id": "doc"}]

    pairs, collect_class_chunks = await _chunk_pairs(
        attribute_and_endpoint_chunks=selected, object_class_chunks=CLASS_CHUNKS, attributes=ATTRIBUTES
    )

    assert pairs == selected
    collect_class_chunks.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoints", [None, EMPTY_ENDPOINTS])
async def test_without_attributes_and_endpoints_the_object_class_chunks_are_used(endpoints):
    pairs, _ = await _chunk_pairs(attribute_and_endpoint_chunks=None, object_class_chunks=CLASS_CHUNKS)

    assert pairs == CLASS_CHUNKS


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoints", [None, EMPTY_ENDPOINTS])
async def test_without_any_data_or_relevant_chunks_no_chunk_is_selected(endpoints):
    pairs, _ = await _chunk_pairs(attribute_and_endpoint_chunks=None, object_class_chunks=[], endpoints=endpoints)

    # An empty list, not None: None would mean "read all documentation".
    assert pairs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attributes", "endpoints", "preferred_endpoints"),
    [
        (ATTRIBUTES, None, None),
        (EMPTY_ATTRIBUTES, ENDPOINTS, None),
        (EMPTY_ATTRIBUTES, None, [{"path": "/roles", "method": "GET"}]),
    ],
    ids=["attributes", "endpoints", "preferred-endpoints"],
)
async def test_data_without_any_relevance_keeps_reading_all_documentation(attributes, endpoints, preferred_endpoints):
    pairs, _ = await _chunk_pairs(
        attribute_and_endpoint_chunks=None,
        object_class_chunks=[],
        attributes=attributes,
        endpoints=endpoints,
        preferred_endpoints=preferred_endpoints,
    )

    assert pairs is None


def _operation_kwargs(operation: str, protocol: ApiType) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "attributes": EMPTY_ATTRIBUTES,
        "endpoints": EMPTY_ENDPOINTS if protocol == ApiType.REST else None,
        "session_id": uuid4(),
        "object_class": "role",
        "job_id": uuid4(),
        "protocol": protocol,
    }
    if operation == "search":
        kwargs["intent"] = SearchIntent.ALL
    return kwargs


@pytest.mark.asyncio
async def test_operation_without_attributes_and_endpoints_runs_the_llm_on_object_class_chunks():
    kwargs = _operation_kwargs("search", ApiType.REST)
    documentation = [
        {"chunkId": "role-chunk", "docId": "role-doc", "content": "GET /roles lists every role."},
        {"chunkId": "other-chunk", "docId": "other-doc", "content": "Unrelated product overview."},
    ]
    chain = AsyncMock()
    chain.ainvoke.return_value = ""

    with (
        patch("src.modules.codegen.generation.get_session_connection_target", new=AsyncMock(return_value=("", ""))),
        patch("src.modules.codegen.generation._collect_relevant_chunks", new=AsyncMock(return_value=None)),
        patch("src.modules.codegen.generation._collect_object_class_chunks", new=AsyncMock(return_value=CLASS_CHUNKS)),
        patch.object(BaseGroovyGenerator, "_load_documentation_items", new=AsyncMock(return_value=documentation)),
        patch.object(BaseGroovyGenerator, "_initialize_progress", new=AsyncMock()),
        patch.object(BaseGroovyGenerator, "_build_llm_chain", return_value=chain),
        patch("src.modules.codegen.core.base.increment_processed_documents", new=AsyncMock()),
        patch("src.modules.codegen.core.base.append_job_error", new=AsyncMock()),
    ):
        await generation.generate_search_code(**kwargs)

    # One LLM call, on the class's chunk only - not on every documentation chunk.
    chain.ainvoke.assert_awaited_once()
    prompt_input = str(chain.ainvoke.await_args)
    assert "GET /roles lists every role." in prompt_input
    assert "Unrelated product overview." not in prompt_input


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
@pytest.mark.parametrize("operation", ["search", "create", "update", "delete"])
async def test_operation_with_nothing_known_returns_its_fallback_without_llm(protocol, operation):
    kwargs = _operation_kwargs(operation, protocol)

    with (
        patch("src.modules.codegen.generation.get_session_connection_target", new=AsyncMock(return_value=("", ""))),
        patch("src.modules.codegen.generation._collect_relevant_chunks", new=AsyncMock(return_value=None)),
        patch("src.modules.codegen.generation._collect_object_class_chunks", new=AsyncMock(return_value=[])),
        patch.object(
            BaseGroovyGenerator,
            "_load_documentation_items",
            new=AsyncMock(return_value=[{"chunkId": str(uuid4()), "content": "Unrelated product overview"}]),
        ),
        patch.object(BaseGroovyGenerator, "_build_llm_chain", side_effect=AssertionError("LLM must not be called")),
        patch("src.modules.codegen.core.base.append_job_error", new=AsyncMock()) as errors,
    ):
        result = await getattr(generation, f"generate_{operation}_code")(**kwargs)

    if protocol == ApiType.REST:
        assert result["code"] == rest_operation_placeholder_artifact("role", operation)
        errors.assert_awaited_once_with(kwargs["job_id"], NO_DOCUMENTED_BEHAVIOR)
    else:
        assert result["code"] == operation_defaults_artifact("role", operation)
        errors.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
async def test_native_schema_without_attributes_finishes_empty_without_llm(protocol):
    job_id = uuid4()
    with (
        patch("src.modules.codegen.generation.generate_groovy", new=AsyncMock()) as model,
        patch("src.modules.codegen.generation.append_job_error", new=AsyncMock()) as errors,
    ):
        # SQL carries no sqlContext here: without attributes there is no physical table to require.
        result = await generation.generate_native_schema_code(
            EMPTY_ATTRIBUTES, "role", session_id=uuid4(), job_id=job_id, protocol=protocol
        )

    assert result == {"format": None, "code": ""}
    model.assert_not_awaited()
    errors.assert_awaited_once_with(job_id, NO_ATTRIBUTES_EXTRACTED)


@pytest.mark.asyncio
async def test_sql_native_schema_repair_without_attributes_runs_with_an_empty_sql_context():
    repair_context = CodegenRepairContext(currentScript="{}", midpointErrors=["Reported error"])
    with patch("src.modules.codegen.generation.generate_groovy", new=AsyncMock(return_value="{}")) as model:
        result = await generation.generate_native_schema_code(
            EMPTY_ATTRIBUTES,
            "role",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.SQL,
            repair_context=repair_context,
        )

    assert result["code"] == "{}"
    prompt_vars = model.await_args.kwargs["extra_prompt_vars"]
    assert prompt_vars["sql_physical_table_json"] == "{}"
    assert prompt_vars["sql_connector_object_class_json"] == "{}"


def _native_schema_repo(attributes: dict) -> MagicMock:
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value=attributes)
    return repo


@pytest.mark.asyncio
async def test_sql_native_schema_scheduling_accepts_empty_attributes():
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
        ) as schedule,
        patch("src.modules.codegen.orchestration.persist_job_pointer", new_callable=AsyncMock),
    ):
        scheduled = await schedule_native_schema_job(
            repo=_native_schema_repo(EMPTY_ATTRIBUTES),
            session_id=uuid4(),
            object_class="role",
            api_type=None,
            skip_cache=False,
            codegen_input=None,
        )

    assert scheduled == job_id
    schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_sql_native_schema_scheduling_still_rejects_attributes_without_sql_context():
    with (
        patch(
            "src.modules.codegen.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as schedule,
        pytest.raises(SqlPhysicalSchemaNotFoundError),
    ):
        await schedule_native_schema_job(
            repo=_native_schema_repo(ATTRIBUTES),
            session_id=uuid4(),
            object_class="role",
            api_type=None,
            skip_cache=False,
            codegen_input=None,
        )

    schedule.assert_not_awaited()
