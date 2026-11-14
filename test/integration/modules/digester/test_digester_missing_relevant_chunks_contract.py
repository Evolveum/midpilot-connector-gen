# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""HTTP contract for attribute and endpoint extraction without relevant documentation chunks.

midPoint used to receive ``400 relevant_chunks_not_found`` from the POST and could not continue
configuring the object class. The POST now returns the documented ``200 {jobId}``; the job
finishes with an empty result and carries the same message in ``errors``.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.app import create_api
from src.auth.context import AuthContext, AuthMode
from src.auth.dependencies import authenticate_request
from src.config import config
from src.core.db import get_db
from src.documents.selection import DocumentationSelection
from src.jobs.schema import JobStatusMultiDocResponse
from src.modules.digester.schemas import AttributeResponse, EndpointResponse
from src.shared.enums import ApiType, JobStatus

OVERVIEW_ONLY = [
    {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": "https://docs.example.com/overview",
        "summary": "Product overview",
        "content": "A product overview that documents no object class.",
        "@metadata": {"category": "overview", "tags": [], "num_endpoints": 0},
    }
]


def _client() -> TestClient:
    async def authenticate(request: Request):
        request.state.auth = AuthContext(AuthMode.dev)

    app = create_api()
    app.dependency_overrides[authenticate_request] = authenticate
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def _session_repo() -> MagicMock:
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": "role", "superclass": ""}]})
    repo.update_session = AsyncMock()
    return repo


@pytest.mark.parametrize(
    ("target", "protocol"),
    [("attributes", ApiType.REST), ("attributes", ApiType.SQL), ("endpoints", ApiType.REST)],
)
def test_extraction_without_relevant_chunks_returns_a_job_instead_of_400(target, protocol):
    session_id = uuid4()
    job_id = uuid4()
    repo = _session_repo()
    relevant_repo = MagicMock(get_relevant_chunks_grouped_by_entity=AsyncMock(return_value={}))

    with (
        patch(f"src.modules.digester.routes.{target}.SessionRepository", return_value=repo),
        patch(
            "src.modules.digester.selection.documentation_selector.load_documentation_items",
            new=AsyncMock(return_value=OVERVIEW_ONLY),
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.RelevantChunkRepository",
            return_value=relevant_repo,
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_base_api_url",
            new=AsyncMock(return_value="https://api.example.com"),
        ),
        patch(
            "src.modules.digester.orchestration.resolve_effective_api_type",
            new_callable=AsyncMock,
            return_value=protocol,
        ),
        patch(
            "src.modules.digester.orchestration.schedule_coroutine_job",
            new_callable=AsyncMock,
            return_value=job_id,
        ) as schedule,
    ):
        response = _client().post(f"{config.app.api_base_url}/v1/digester/{session_id}/classes/role/{target}")

    assert response.status_code == 200
    assert response.json() == {"jobId": str(job_id)}
    schedule_kwargs = schedule.call_args.kwargs
    assert schedule_kwargs["initial_errors"] == [
        f"No relevant chunks found for object class 'role'. Cannot extract {target}."
    ]
    # Nothing to read: the worker returns the empty result without an LLM call.
    selection = DocumentationSelection.model_validate(schedule_kwargs["input_payload"]["documentationSelection"])
    assert selection.chunks == ()
    # The job pointer is stored, so the GET status request finds this job.
    pointer = repo.update_session.await_args.args[1]
    assert pointer[f"role{target.title()}JobId"] == str(job_id)


@pytest.mark.parametrize(
    ("target", "empty_result", "refresh"),
    [
        ("attributes", AttributeResponse(attributes={}), "refresh_attributes_status"),
        ("endpoints", EndpointResponse(endpoints=[]), "refresh_endpoints_status"),
    ],
)
def test_finished_job_reports_the_empty_result_together_with_the_error(target, empty_result, refresh):
    session_id = uuid4()
    job_id = uuid4()
    message = f"No relevant chunks found for object class 'role'. Cannot extract {target}."
    repo = MagicMock()
    repo.session_exists = AsyncMock(return_value=True)
    repo.get_session_data = AsyncMock(return_value=str(job_id))
    status = JobStatusMultiDocResponse(
        jobId=job_id,
        status=JobStatus.finished,
        result=empty_result,
        errors=[message],
    )

    with (
        patch(f"src.modules.digester.routes.{target}.SessionRepository", return_value=repo),
        patch(
            f"src.modules.digester.routes.{target}.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=status,
        ),
        patch(f"src.modules.digester.routes.{target}.results.{refresh}", new_callable=AsyncMock, return_value=status),
    ):
        response = _client().get(f"{config.app.api_base_url}/v1/digester/{session_id}/classes/role/{target}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "finished"
    assert body["result"][target] == ({} if target == "attributes" else [])
    assert body["errors"] == [message]
