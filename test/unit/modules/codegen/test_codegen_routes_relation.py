# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the codegen relation routes and their job scheduling."""

from unittest.mock import ANY, AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.jobs import job_input_reference
from src.modules.codegen.routes.relations import generate_relation_code, get_relation_code_status
from src.modules.digester.errors import InvalidRelationsOutputError, RelationNotFoundError
from src.shared.enums import JobStatus


def _session_data_reader(**payloads):
    """Read the session keys the relation route asks for, one payload per key."""

    async def read(_session_id, key):
        return payloads.get(key)

    return AsyncMock(side_effect=read)


# RELATION
@pytest.mark.asyncio
async def test_generate_relation_code_success():
    """Test successful generation of relation code."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "User",
                "object": "Group",
                "subjectAttribute": "members",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "user_to_group",
                "displayName": "User to Group",
            }
        ]
    }
    mock_repo.get_session_data = _session_data_reader(relationsOutput=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_relation_code(session_id, "user_to_group", db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        assert [call.args[1] for call in mock_repo.get_session_data.await_args_list] == [
            "relationsOutput",
            "relationsAnalysisOutput",
        ]
        mock_schedule.assert_awaited_once()
        schedule_kwargs = mock_schedule.await_args.kwargs
        assert schedule_kwargs["input_payload"]["relationName"] == "user_to_group"
        assert [item["name"] for item in schedule_kwargs["input_payload"]["relations"]["relations"]] == [
            "user_to_group"
        ]
        # No stored analysis: the job still records the absence explicitly.
        assert schedule_kwargs["input_payload"]["relationContext"] is None
        assert schedule_kwargs["worker_kwargs"]["relations"] == job_input_reference("relations")
        assert schedule_kwargs["worker_kwargs"]["relation_name"] == "user_to_group"
        assert schedule_kwargs["worker_kwargs"]["relation_context"] == job_input_reference("relationContext")
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_relation_code_selects_relation_by_name():
    """Only the requested relation should be passed to code generation."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "principal",
                "object": "role",
                "subjectAttribute": "roles",
                "objectAttribute": "",
                "shortDescription": "Principal receives a role.",
                "name": "principal_to_role",
                "displayName": "Principal to Role",
            },
            {
                "subject": "principal",
                "object": "membership",
                "subjectAttribute": "memberships",
                "objectAttribute": "",
                "shortDescription": "Principal has memberships.",
                "name": "principal_to_membership",
                "displayName": "Principal to Membership",
            },
        ]
    }
    mock_repo.get_session_data = _session_data_reader(relationsOutput=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        session_id = uuid4()
        mock_schedule.return_value = job_id

        response = await generate_relation_code(session_id, "principal_to_membership", db=MagicMock())

    assert response.jobId == job_id
    schedule_kwargs = mock_schedule.await_args.kwargs
    assert schedule_kwargs["input_payload"]["relations"] == {
        "relations": [
            {
                "name": "principal_to_membership",
                "displayName": "Principal to Membership",
                "shortDescription": "Principal has memberships.",
                "subject": "principal",
                "subjectAttribute": "memberships",
                "object": "membership",
                "objectAttribute": "",
            }
        ]
    }
    assert schedule_kwargs["worker_kwargs"]["relations"] == job_input_reference("relations")


@pytest.mark.asyncio
async def test_generate_relation_code_snapshots_link_object_context():
    """An association carried by a third class must reach the job with that class named."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "user",
                "object": "group",
                "subjectAttribute": "",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "user_to_group",
                "displayName": "User to Group",
            }
        ]
    }
    analysis_payload = {
        "pairs": [
            {
                "pairKey": "group|user",
                "classA": "Group",
                "classB": "User",
                "accepted": True,
                "observations": [
                    {
                        "sourceClass": "User",
                        "targetClass": "Group",
                        "evidenceKind": "schema_reference",
                        "viaClass": "Membership",
                    }
                ],
                "decisions": [
                    {
                        "accepted": True,
                        "verdict": {
                            "isRelation": True,
                            "kind": "link_object",
                            "subject": "User",
                            "object": "Group",
                            "linkObjectClass": "Membership",
                            "name": "user_to_group",
                        },
                    }
                ],
            },
            {
                "pairKey": "membership|user",
                "classA": "Membership",
                "classB": "User",
                "observations": [
                    {
                        "sourceClass": "Membership",
                        "targetClass": "User",
                        "sourceAttribute": "userId",
                        "evidenceKind": "attribute_metadata",
                    }
                ],
            },
            {
                "pairKey": "group|membership",
                "classA": "Group",
                "classB": "Membership",
                "observations": [
                    {
                        "sourceClass": "Membership",
                        "targetClass": "Group",
                        "sourceAttribute": "groupId",
                        "evidenceKind": "attribute_metadata",
                    }
                ],
            },
        ]
    }
    mock_repo.get_session_data = _session_data_reader(
        relationsOutput=relations_payload,
        relationsAnalysisOutput=analysis_payload,
    )
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = uuid4()

        await generate_relation_code(uuid4(), "user_to_group", db=MagicMock())

    schedule_kwargs = mock_schedule.await_args.kwargs
    assert schedule_kwargs["input_payload"]["relationContext"] == {
        "kind": "link_object",
        "linkObjectClass": "Membership",
        "linkAttributes": [
            {"attribute": "userId", "references": "user"},
            {"attribute": "groupId", "references": "group"},
        ],
    }


@pytest.mark.asyncio
async def test_generate_relation_code_rejects_missing_display_name():
    """Relation codegen should require the current RelationsResponse format."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
        "relations": [
            {
                "subject": "user",
                "object": "group",
                "subjectAttribute": "groups",
                "objectAttribute": "",
                "shortDescription": "",
                "name": "user_to_group",
            }
        ]
    }
    mock_repo.get_session_data = _session_data_reader(relationsOutput=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        session_id = uuid4()

        with pytest.raises(InvalidRelationsOutputError) as exc_info:
            await generate_relation_code(session_id, "user_to_group", db=MagicMock())

    assert exc_info.value.status_code == 422
    assert "Stored relationsOutput is invalid" in exc_info.value.message
    mock_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_relation_code_rejects_unknown_relation_name():
    """Relation codegen should fail before scheduling when the route name is absent."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    relations_payload = {
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
    mock_repo.get_session_data = _session_data_reader(relationsOutput=relations_payload)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.modules.codegen.orchestration.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        session_id = uuid4()

        with pytest.raises(RelationNotFoundError) as exc_info:
            await generate_relation_code(session_id, "principal_to_role", db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "Relation principal_to_role not found" in exc_info.value.message
    mock_schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_relation_code_status_found():
    """Test getting relation code generation status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_job_repo = MagicMock()
    mock_job_repo.get_job_for_session = AsyncMock(return_value=MagicMock())

    fake_status = MagicMock(
        jobId=ANY,
        status=JobStatus.finished,
        result="mocked relation code",
        progress=None,
        errors=None,
    )

    with (
        patch("src.modules.codegen.routes.relations.SessionRepository", return_value=mock_repo),
        patch("src.session.access.JobRepository", return_value=mock_job_repo),
        patch(
            "src.modules.codegen.routes.relations.build_multi_doc_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_builder,
    ):
        job_id = uuid4()
        session_id = uuid4()

        response = await get_relation_code_status(session_id, "membership", job_id, db=MagicMock())

        assert response.status == JobStatus.finished
        assert response.result == "mocked relation code"
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_job_repo.get_job_for_session.assert_awaited_once_with(job_id, session_id)
        mock_builder.assert_awaited_once_with(job_id)
