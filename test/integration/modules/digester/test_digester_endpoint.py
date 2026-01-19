# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Integration tests for digester endpoints."""

from unittest.mock import ANY, AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.common.enums import JobStatus
from src.modules.digester import service
from src.modules.digester.router import (
    extract_auth,
    extract_class_attributes,
    extract_class_endpoints,
    extract_metadata,
    extract_object_classes,
    extract_relations,
    get_auth_status,
    get_class_attributes_status,
    get_class_endpoints_status,
    get_metadata_status,
    get_object_classes_status,
    get_relations_status,
    override_class_attributes,
    override_class_endpoints,
    override_relations,
)
from src.modules.digester.schema import (
    AttributeInfo,
    AuthInfo,
    AuthResponse,
    EndpointInfo,
    EndpointsResponse,
    InfoResponse,
    ObjectClassSchemaResponse,
    RelationsResponse,
)

# CLASSES (object classes)


@pytest.mark.asyncio
async def test_extract_object_classes_success():
    """Test successful extraction of object classes."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"uuid": str(uuid4()), "content": "fake content for testing"}]

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=fake_docs,
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_object_classes(session_id, db=MagicMock())

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_object_classes_session_not_found():
    """Test extraction with non-existent session."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=False)

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        with pytest.raises(HTTPException) as exc_info:
            await extract_object_classes(uuid4(), db=MagicMock())

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_get_object_classes_status_found():
    """Test getting object classes status when job exists."""
    mock_repo = MagicMock()
    job_id = uuid4()

    object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "description": "User's description",
                "relevantChunks": [],
            },
            {
                "name": "Group",
                "relevant": "true",
                "description": "Group's description",
                "relevantChunks": [],
            },
        ]
    }

    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(side_effect=[str(job_id), object_classes_output])

    mock_status = {
        "jobId": job_id,
        "status": "finished",
        "result": {
            "objectClasses": [
                {"name": "User", "relevant": "true", "description": "User's description"},
                {"name": "Group", "relevant": "true", "description": "Group's description"},
            ]
        },
    }

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.get_job_status", new_callable=AsyncMock, return_value=mock_status),
    ):
        session_id = uuid4()
        response = await get_object_classes_status(session_id, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.objectClasses) == 2
    assert response.result.objectClasses[0].name == "User"
    assert response.result.objectClasses[1].name == "Group"


# CLASS ATTRIBUTES
@pytest.mark.asyncio
async def test_extract_class_attributes_success():
    """Test successful extraction of class attributes."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]

    # Mock objectClassesOutput with relevant chunks for the User class
    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantChunks": [
                    {"docUuid": "doc-1"},
                    {"docUuid": "doc-1"},
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
            object_class="User",
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
            "src.modules.digester.router._build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=MagicMock(jobId=job_id, status=JobStatus.finished, result=None),
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_class_attributes_status(
            session_id=session_id,
            object_class="User",
            jobId=None,
            db=MagicMock(),
        )

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert isinstance(response.result, ObjectClassSchemaResponse)
    assert "id" in response.result.attributes
    assert response.result.attributes["id"].type == "string"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    assert mock_repo.get_session_data.await_args_list == [
        call(session_id, "UserAttributesJobId"),
        call(session_id, "UserAttributesOutput"),
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
    mock_repo.update_session.assert_awaited_once_with(session_id, {"UserAttributesOutput": {"id": {"type": "string"}}})
    assert response["message"].startswith("Attributes for User overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "User"


# CLASS ENDPOINTS
@pytest.mark.asyncio
async def test_extract_class_endpoints_success():
    """Test successful extraction of endpoints for object class."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]

    # Mock objectClassesOutput with relevant chunks for the User class
    mock_object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "superclass": "",
                "abstract": False,
                "embedded": False,
                "description": "Represents a user",
                "relevantChunks": [
                    {"docUuid": "doc-1"},
                    {"docUuid": "doc-1"},
                ],
                "endpoints": [],
            }
        ]
    }

    # Mock metadataOutput for baseApiUrl
    mock_metadata_output = {"infoAboutSchema": {"baseApiEndpoint": [{"uri": "https://api.example.com"}]}}

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(side_effect=[mock_object_classes_output, mock_metadata_output])
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[{"uuid": "doc-1"}],
        ),
        patch(
            "src.modules.digester.router.get_session_documentation",
            new=AsyncMock(return_value=fake_docs),
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_class_endpoints(
            session_id=session_id,
            object_class="User",
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        assert mock_repo.get_session_data.await_count == 2
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_class_endpoints_status_found():
    """Test getting endpoints extraction status when job exists."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=EndpointsResponse(endpoints=[EndpointInfo(method="GET", path="/users", description="List users")]),
    )

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router._build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
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
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "UserEndpointsJobId")
    mock_status_builder.assert_awaited_once()


@pytest.mark.asyncio
async def test_override_class_endpoints_success():
    """Test manual override of endpoints."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
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
        {"UserEndpointsOutput": {"listUsers": {"method": "GET", "path": "/users"}}},
    )
    assert response["message"].startswith("Endpoints for User overridden successfully")
    assert response["sessionId"] == session_id
    assert response["objectClass"] == "User"


# RELATIONS
@pytest.mark.asyncio
async def test_extract_relations_success():
    """Test extracting relations, given that relevant object classes exist in session."""
    session_id = uuid4()
    job_id = uuid4()

    fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]

    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": "User", "relevant": "true"}]})
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=fake_docs,
        ),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_schedule.return_value = job_id

        response = await extract_relations(
            session_id=session_id,
            db=MagicMock(),
        )

        assert response.jobId == job_id
        mock_repo.session_exists.assert_awaited_once_with(session_id)
        mock_repo.get_session_data.assert_awaited_once_with(session_id, "objectClassesOutput")
        mock_schedule.assert_awaited_once()
        mock_repo.update_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_relations_no_classes():
    """If there are no relevant object classes in the session, it should raise 404."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.filter_documentation_items", new_callable=AsyncMock, return_value=[]),
    ):
        session_id = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await extract_relations(session_id=session_id, db=MagicMock())

    assert exc_info.value.status_code == 404
    assert "no object classes" in exc_info.value.detail.lower()
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
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router._build_typed_job_status_response",
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

    relations_payload = {"relations": [{"from": "User", "to": "Group", "type": "membership"}]}

    with patch("src.modules.digester.router.SessionRepository", return_value=mock_repo):
        session_id = uuid4()
        response = await override_relations(
            session_id=session_id,
            relations=relations_payload,
            db=MagicMock(),
        )

    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.update_session.assert_awaited_once_with(session_id, {"relationsOutput": relations_payload})
    assert response["message"].startswith("Relations overridden successfully")
    assert response["sessionId"] == session_id


# AUTH
@pytest.mark.asyncio
async def test_extract_auth_success():
    """Test extracting auth info."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        mock_schedule.return_value = job_id

        session_id = uuid4()
        response = await extract_auth(session_id=session_id, db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_schedule.assert_awaited_once_with(
        job_type="digester.getAuth",
        input_payload={},
        dynamic_input_enabled=True,
        dynamic_input_provider=ANY,
        worker=service.extract_auth,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="authOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )
    mock_repo.update_session.assert_awaited_once_with(session_id, {"authJobId": str(job_id)})


@pytest.mark.asyncio
async def test_get_auth_status_found():
    """Test getting auth extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(
        jobId=job_id,
        status=JobStatus.finished,
        result=AuthResponse(auth=[AuthInfo(name="OAuth2", type="oauth2")]),
    )

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router._build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_auth_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    assert len(response.result.auth) == 1
    assert response.result.auth[0].name == "OAuth2"
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "authJobId")
    mock_status_builder.assert_awaited_once()


# METADATA
@pytest.mark.asyncio
async def test_extract_metadata_success():
    """Test extracting API metadata."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch("src.modules.digester.router.schedule_coroutine_job", new_callable=AsyncMock) as mock_schedule,
    ):
        job_id = uuid4()
        mock_schedule.return_value = job_id

        session_id = uuid4()
        response = await extract_metadata(session_id=session_id, db=MagicMock())

    assert response.jobId == job_id
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_schedule.assert_awaited_once_with(
        job_type="digester.getInfoMetadata",
        input_payload={},
        dynamic_input_enabled=True,
        dynamic_input_provider=ANY,
        worker=service.extract_info_metadata,
        worker_kwargs={},
        initial_stage="chunking",
        initial_message="Preparing and splitting documentation",
        session_id=session_id,
        session_result_key="metadataOutput",
        await_documentation=True,
        await_documentation_timeout=750,
    )
    mock_repo.update_session.assert_awaited_once_with(session_id, {"metadataJobId": str(job_id)})


@pytest.mark.asyncio
async def test_get_metadata_status_found():
    """Test getting metadata extraction status."""
    mock_repo = MagicMock()
    mock_repo.session_exists = AsyncMock(return_value=True)
    job_id = uuid4()
    mock_repo.get_session_data = AsyncMock(return_value=str(job_id))

    fake_status = MagicMock(jobId=job_id, status=JobStatus.finished, result=InfoResponse())

    with (
        patch("src.modules.digester.router.SessionRepository", return_value=mock_repo),
        patch(
            "src.modules.digester.router._build_typed_job_status_response",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_status_builder,
    ):
        session_id = uuid4()
        response = await get_metadata_status(session_id=session_id, jobId=None, db=MagicMock())

    assert response.jobId == job_id
    assert response.status == JobStatus.finished
    mock_repo.session_exists.assert_awaited_once_with(session_id)
    mock_repo.get_session_data.assert_awaited_once_with(session_id, "metadataJobId")
    mock_status_builder.assert_awaited_once()
