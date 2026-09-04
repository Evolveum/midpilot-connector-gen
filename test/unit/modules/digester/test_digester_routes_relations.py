# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the digester relations routes and their job scheduling."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs import job_input_reference
from src.modules.digester.errors import ObjectClassesNotFoundError
from src.modules.digester.routes.relations import extract_relations, get_relations_status, override_relations
from src.modules.digester.schemas import RelationsResponse
from src.modules.digester.selection import RELATION_CRITERIA
from src.shared.enums import ApiType, JobStatus


# RELATIONS
@pytest.mark.asyncio
async def test_extract_relations_success():
    """Test extracting relations, given that relevant object classes exist in session."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"docId": "page-1", "chunkId": "doc-1", "content": "fake content for testing"}]
    attribute_output = {"attributes": {"groups": {"type": "Group", "format": "reference"}}}
    endpoint_output = {"endpoints": [{"method": "GET", "path": "/Users/{id}/Groups"}]}

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": "User", "relevant": "true"}]})
    mock_repo.get_session_values = AsyncMock(
        return_value={
            "userAttributesOutput": attribute_output,
            "userEndpointsOutput": endpoint_output,
        }
    )
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.routes.relations.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.orchestration.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=fake_docs,
        ) as mock_filter,
        patch("src.modules.digester.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_relations(
            session_id=session_id,
            skip_cache=False,
            api_type=ApiType.REST,
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")
        mock_filter.assert_awaited_once_with(RELATION_CRITERIA, session_id, db=ANY)
        mock_repo.get_session_values.assert_awaited_once_with(
            session_id,
            ["userAttributesOutput", "userEndpointsOutput"],
        )
        mock_schedule.assert_awaited_once()
        schedule_kwargs = mock_schedule.await_args.kwargs
        assert schedule_kwargs["input_payload"]["classSchemaSnapshot"] == {
            "attributesByClass": {"user": attribute_output},
            "endpointsByClass": {"user": endpoint_output},
        }
        assert schedule_kwargs["input_payload"]["apiType"] == "rest"
        assert schedule_kwargs["worker_args"][2] == job_input_reference("classSchemaSnapshot")
        assert schedule_kwargs["worker_args"][3] == job_input_reference("apiType")
        assert schedule_kwargs["session_companion_result_keys"] == ("relationsAnalysisOutput",)
        mock_repo.update_session.assert_awaited_once()
        assert mock_repo.update_session.await_args.args[1]["relationsInput"]["apiType"] == "rest"


@pytest.mark.asyncio
async def test_extract_relations_no_classes():
    """If there are no relevant object classes in the session, it should raise 404."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with (
        patch("src.modules.digester.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.orchestration.filter_documentation_items", new_callable=AsyncMock, return_value=[]),
    ):
        session_id = uuid4()
        with pytest.raises(ObjectClassesNotFoundError) as exc_info:
            await extract_relations(session_id=session_id, api_type=ApiType.REST, db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "no object classes" in exc_info.value.message.lower()
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")


@pytest.mark.asyncio
async def test_get_relations_status_found():
    """Test getting relations extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=RelationsResponse(relations=[]),
    )

    with (
        patch("src.modules.digester.routes.relations.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.routes.relations.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_relations_status(
            session_id=session_id,
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "relationsJobId")
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_relations_success():
    """Test manual override of relations."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    relations_payload = RelationsResponse.model_validate(
        {
            "relations": [
                {
                    "subject": "user",
                    "object": "group",
                    "subjectAttribute": "groups",
                    "objectAttribute": "",
                    "shortDescription": "",
                    "name": "user_to_group",
                    "displayName": "User to Group",
                }
            ]
        }
    )

    with patch("src.modules.digester.routes.relations.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_relations(
            session_id=session_id,
            relations=relations_payload,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "relationsOutput": relations_payload.model_dump(by_alias=True, mode="json"),
            "relationsAnalysisOutput": None,
        },
    )
    assert response["message"].startswith("Relations overridden successfully")
    assert response["sessionId"] == session_id
