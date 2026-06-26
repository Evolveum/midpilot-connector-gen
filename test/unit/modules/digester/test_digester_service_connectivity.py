# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, call, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.schemas import ExtractedConnectivityEndpointInfo
from src.modules.digester.selection import CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA


@pytest.mark.asyncio
async def test_extract_connectivity_endpoint_returns_all_candidates_ranked(mock_digester_update_job_progress):
    session_id = uuid4()
    job_id = uuid4()
    doc_id_1 = str(uuid4())
    doc_id_2 = str(uuid4())
    chunk_id_1 = uuid4()
    chunk_id_2 = uuid4()
    doc_items = [
        {"docId": doc_id_1, "chunkId": str(chunk_id_1), "content": "POST /users"},
        {"docId": doc_id_2, "chunkId": str(chunk_id_2), "content": "GET /me and GET /status"},
    ]

    create_candidate = ExtractedConnectivityEndpointInfo(
        path="/users",
        method=EndpointMethod.POST,
        description="Creates a user",
        requires_auth=True,
    )
    status_candidate = ExtractedConnectivityEndpointInfo(
        path="/status",
        method=EndpointMethod.GET,
        description="Public status endpoint",
        requires_auth=False,
    )
    me_candidate = ExtractedConnectivityEndpointInfo(
        path="/me",
        method=EndpointMethod.GET,
        description="Returns current authenticated user profile",
        requires_auth=True,
    )

    with (
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_run,
        patch(
            "src.modules.digester.extractors.connectivity_endpoint.rank_connectivity_candidates",
            new_callable=AsyncMock,
        ) as mock_rank,
    ):
        mock_run.return_value = [
            ([create_candidate], True, chunk_id_1),
            ([status_candidate, me_candidate], True, chunk_id_2),
        ]

        # Simulate rank returning /me first
        async def fake_rank(candidates, job_id):
            by_path = {c.path: c for c in candidates}
            return [by_path["/me"], by_path["/status"], by_path["/users"]]

        mock_rank.side_effect = fake_rank

        result = await service.extract_connectivity_endpoint(
            doc_items=doc_items,
            session_id=session_id,
            job_id=job_id,
            base_api_url="https://api.example.com/api/v1/",
        )

    endpoints = result["result"]["endpoints"]
    assert len(endpoints) == 3
    assert endpoints[0]["path"] == "/me"
    assert endpoints[0]["method"] == "GET"
    assert endpoints[0]["requiresAuth"] is True
    assert endpoints[1]["path"] == "/status"
    assert endpoints[2]["path"] == "/users"
    assert result["relevantDocumentations"] == [
        {"doc_id": doc_id_1, "chunk_id": str(chunk_id_1)},
        {"doc_id": doc_id_2, "chunk_id": str(chunk_id_2)},
    ]
    mock_run.assert_awaited_once()
    mock_digester_update_job_progress.assert_awaited()


@pytest.mark.asyncio
async def test_extract_connectivity_endpoint_retries_with_fallback_when_primary_is_empty(
    mock_digester_update_job_progress,
):
    session_id = uuid4()
    job_id = uuid4()
    primary_chunk_id = str(uuid4())
    fallback_chunk_id = str(uuid4())
    primary_doc_items = [{"docId": str(uuid4()), "chunkId": primary_chunk_id, "content": "overview"}]
    fallback_doc_items = [
        *primary_doc_items,
        {"docId": str(uuid4()), "chunkId": fallback_chunk_id, "content": "GET /status"},
    ]
    empty_result = {"result": {"endpoints": []}, "relevantDocumentations": []}
    fallback_result = {
        "result": {
            "endpoints": [
                {
                    "path": "/status",
                    "method": "GET",
                    "description": "Status endpoint",
                    "requiresAuth": False,
                    "relevantDocumentations": [],
                }
            ]
        },
        "relevantDocumentations": [{"doc_id": "doc-2", "chunk_id": fallback_chunk_id}],
    }

    with (
        patch(
            "src.modules.digester.service._extract_connectivity_endpoint_from_doc_items",
            new_callable=AsyncMock,
        ) as mock_extract,
        patch("src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
    ):
        mock_extract.side_effect = [empty_result, fallback_result]
        mock_filter.return_value = fallback_doc_items

        result = await service.extract_connectivity_endpoint(
            doc_items=primary_doc_items,
            session_id=session_id,
            job_id=job_id,
        )

    assert result == fallback_result
    mock_filter.assert_awaited_once_with(CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, session_id)
    mock_extract.assert_has_awaits(
        [
            call(primary_doc_items, job_id, ""),
            call([fallback_doc_items[1]], job_id, ""),
        ]
    )
    mock_digester_update_job_progress.assert_awaited()
