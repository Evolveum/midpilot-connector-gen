# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the digester class-endpoints routes and their job scheduling."""

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.documents.selection import DocumentationSelection, SelectionRole
from src.jobs import job_input_reference
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.errors import EndpointExtractionNotSupportedError
from src.modules.digester.routes.endpoints import (
    extract_class_endpoints,
    get_class_endpoints_status,
    override_class_endpoints,
)
from src.modules.digester.schemas import EndpointInfo, EndpointResponse
from src.shared.enums import ApiType, JobStatus


# CLASS ENDPOINTS
@pytest.mark.asyncio
async def test_extract_class_endpoints_success():
    """The job input stores the selection, the protocol, the base URL and the structural flags."""
    session_id = uuid4()
    job_id = uuid4()
    spec = {
        "docId": "page-1",
        "chunkId": "doc-1",
        "url": "https://docs.example.com/users",
        "summary": "User endpoints",
        "content": "fake content for testing",
        "@metadata": {"category": "spec_yaml", "tags": ["user", "endpoint"], "num_endpoints": 2},
    }

    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantDocumentations": [{"docId": "page-1", "chunkId": "doc-1"}],
                "endpoints": [],
            }
        ]
    }

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=mock_object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.routes.endpoints.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SCIM,
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_base_api_url",
            new_callable=AsyncMock,
            return_value="https://api.example.com",
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.load_documentation_items",
            new=AsyncMock(return_value=[spec]),
        ),
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_endpoints(
            session_id=session_id,
            object_class="User",
            db=MagicMock(),
            api_type=None,
        )

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    schedule_kwargs = mock_schedule.call_args.kwargs
    input_payload = schedule_kwargs["input_payload"]
    assert input_payload["objectClass"] == "user"
    assert input_payload["apiType"] == "scim"
    assert input_payload["baseApiUrl"] == "https://api.example.com"
    assert input_payload["objectClassFlags"] == {"embedded": False, "abstract": False}
    selection = DocumentationSelection.model_validate(input_payload["documentationSelection"])
    assert selection.relevant_chunks(SelectionRole.PRIMARY) == [{"doc_id": "page-1", "chunk_id": "doc-1"}]
    assert schedule_kwargs["worker_kwargs"] == {
        "selection": job_input_reference("documentationSelection"),
        "object_class": "user",
        "session_id": session_id,
        "protocol": ApiType.SCIM,
        "base_api_url": job_input_reference("baseApiUrl"),
        "object_class_flags": job_input_reference("objectClassFlags"),
    }
    assert schedule_kwargs["session_result_key"] == "userEndpointsOutput"
    mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_class_endpoints_rejects_sql_without_scheduling_a_job():
    """SQL has no endpoint surface; the protocol-specific request is intentionally rejected."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)

    with (
        patch("src.modules.digester.routes.endpoints.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=ApiType.SQL,
        ),
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        with pytest.raises(EndpointExtractionNotSupportedError) as excinfo:
            await extract_class_endpoints(
                session_id=uuid4(),
                object_class="User",
                skip_cache=False,
                api_type=ApiType.SQL,
                db=MagicMock(),
            )

    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "endpoint_extraction_not_supported"
    mock_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_class_endpoints_status_found():
    """Test getting endpoints extraction status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    scim_capabilities = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": False},
        "bulk": {"supported": True, "maxOperations": 15, "maxPayloadSize": 2097152},
        "filter": {"supported": True, "maxResults": 50},
        "changePassword": {"supported": False},
        "sort": {"supported": True},
        "etag": {"supported": False},
        "authenticationSchemes": [],
    }
    mock_repo.get_session_data = AsyncMock(
        side_effect=[
            str(job_id),
            {
                "endpoints": [
                    EndpointInfo(method=EndpointMethod.GET, path="/users", description="List users").model_dump(
                        by_alias=True
                    )
                ],
                "scimCapabilities": scim_capabilities,
            },
        ]
    )

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=EndpointResponse(
            endpoints=[EndpointInfo(method=EndpointMethod.GET, path="/users", description="List users")]
        ),
    )

    with (
        patch("src.modules.digester.routes.endpoints.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.routes.endpoints.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
        patch(
            "src.modules.digester.results.hydrate_endpoints_with_relevance",
            new_callable=AsyncMock,
            side_effect=lambda _db, _session_id, _result_key, payload: payload,
        ),
    ):
        session_id = uuid4()
        response = await get_class_endpoints_status(
            session_id=session_id,
            object_class="User",
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.endpoints) == 1
    assert response.result.endpoints[0].method == "GET"
    assert response.result.endpoints[0].path == "/users"
    assert response.result.scim_capabilities is not None
    assert response.result.scim_capabilities.patch.supported is False
    assert response.result.scim_capabilities.filter.max_results == 50
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    assert mock_repo.get_session_data.await_args_list == [
        call(session_id, "userEndpointsJobId"),
        call(session_id, "userEndpointsOutput"),
    ]
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_class_endpoints_success():
    """Test manual override of endpoints."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()
    mock_relevant_repo = MagicMock()
    mock_relevant_repo.replace_relevant_chunks_for_result = AsyncMock()

    with (
        patch("src.modules.digester.routes.endpoints.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.results.RelevantChunkRepository", return_value=mock_relevant_repo),
    ):
        session_id = uuid4()
        response = await override_class_endpoints(
            session_id=session_id,
            object_class="User",
            endpoints={"listUsers": {"method": "GET", "path": "/users"}},
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {"userEndpointsOutput": {"listUsers": {"method": "GET", "path": "/users"}}},
    )
    mock_relevant_repo.replace_relevant_chunks_for_result.assert_awaited_once()
    assert response["message"].startswith("Endpoints for user overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "user"
