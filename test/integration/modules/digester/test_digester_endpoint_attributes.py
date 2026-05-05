# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester class-attributes endpoints."""

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.common.enums import JobStatus
from src.modules.digester.router import (
    extract_class_attributes,
    get_class_attributes_status,
    override_class_attributes,
)
from src.modules.digester.schema import AttributeInfo, AttributeResponse


# CLASS ATTRIBUTES
@pytest.mark.asyncio
async def test_extract_class_attributes_success():
    """Test successful extraction of class attributes."""
    session_id = uuid4()
    job_id = uuid4()
    chunk_id = str(uuid4())
    doc_id = str(uuid4())

    fake_docs = [{"chunkId": chunk_id, "docId": doc_id, "content": "fake content for testing"}]

    # Mock objectClassesOutput with relevant chunks for the User class
    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "user",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantDocumentations": [
                    {"docId": doc_id, "chunkId": chunk_id},
                    {"docId": doc_id, "chunkId": chunk_id},
                ],
                "endpoints": [],
                "attributes": {},
            }
        ]
    }

    # extract_class_attributes reads objectClassesOutput and documentationItems
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=mock_object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.get_session_documentation",
            new=AsyncMock(return_value=fake_docs),
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_attributes(
            session_id=session_id,
            object_class="user",
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_class_attributes_status_found():
    """Test getting class attributes status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()

    mock_repo.get_session_data = AsyncMock(
        side_effect=[
            str(job_id),
            {
                "attributes": {
                    "id": AttributeInfo(
                        type="string",
                        description="Unique identifier",
                        mandatory=True,
                    ).model_dump(),
                }
            },
        ]
    )

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=MagicMock(jobId=job_id, status=JobStatus.finished, result=None),
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_class_attributes_status(
            session_id=session_id,
            object_class="user",
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert isinstance(response.result, AttributeResponse)
    assert "id" in response.result.attributes
    assert response.result.attributes["id"].type == "string"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    assert mock_repo.get_session_data.await_args_list == [
        call(session_id, "userAttributesJobId"),
        call(session_id, "userAttributesOutput"),
    ]
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_class_attributes_success():
    """Test manual override of class attributes."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_class_attributes(
            session_id=session_id,
            object_class="User",
            attributes={"id": {"type": "string"}},
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(session_id, {"userAttributesOutput": {"id": {"type": "string"}}})
    assert response["message"].startswith("Attributes for user overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "user"
