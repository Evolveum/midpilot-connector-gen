#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

# """Integration tests for digester endpoints."""
#
# from unittest.mock import AsyncMock, MagicMock, patch
# from uuid import uuid4
#
# import pytest
# from fastapi import HTTPException
#
# from src.common.enums import JobStatus
# from src.modules.digester.router import (
#     extract_auth,
#     extract_class_attributes,
#     extract_class_endpoints,
#     extract_metadata,
#     extract_object_classes,
#     extract_relations,
#     get_auth_status,
#     get_class_attributes_status,
#     get_class_endpoints_status,
#     get_metadata_status,
#     get_object_classes_status,
#     get_relations_status,
#     override_class_attributes,
#     override_class_endpoints,
#     override_relations,
# )
#
# # CLASSES (object classes)
#
#
# @pytest.mark.asyncio
# async def test_extract_object_classes_success():
#     """Test successful extraction of object classes."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#     job_id = uuid4()
#
#     fake_docs = [{"uuid": str(uuid4()), "content": "fake content for testing"}]
#     mock_session_manager.get_session_data.return_value = fake_docs
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.common.chunk_filter.filter.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         mock_schedule.return_value = job_id
#
#         response = await extract_object_classes("test-session-id")
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_extract_object_classes_session_not_found():
#     """Test extraction with non-existent session."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = False
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.modules.digester.router.filter_documentation_items",
#             side_effect=ValueError("Session with ID non-existent-session does not exist."),
#         ),
#     ):
#         with pytest.raises(HTTPException) as exc_info:
#             await extract_object_classes("non-existent-session")
#
#         assert exc_info.value.status_code == 404
#         assert "not found" in str(exc_info.value.detail).lower()
#
#
# @pytest.mark.asyncio
# async def test_get_object_classes_status_found():
#     """Test getting object classes status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {
#             "objectClasses": [
#                 {"name": "User", "relevant": "true", "description": "User's description"},
#                 {"name": "Group", "relevant": "true", "description": "Group's description"},
#             ]
#         },
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_object_classes_status(uuid4())
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result is not None
#         assert len(response.result.objectClasses) == 2
#         assert response.result.objectClasses[0].name == "User"
#         assert response.result.objectClasses[1].name == "Group"
#         assert response.result.objectClasses[0].description == "User's description"
#         assert response.result.objectClasses[1].description == "Group's description"
#
#
# # CLASS ATTRIBUTES
# @pytest.mark.asyncio
# async def test_extract_class_attributes_success():
#     """Test successful extraction of class attributes."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]
#
#     # Mock objectClassesOutput with relevant chunks for the User class
#     mock_object_classes_output = {
#         "objectClasses": [
#             {
#                 "name": "User",
#                 "relevant": "true",
#                 "superclass": "",
#                 "abstract": False,
#                 "embedded": False,
#                 "description": "Represents a user",
#                 "relevantChunks": [
#                     {"docUuid": "doc-1", "chunkIndex": 0},
#                     {"docUuid": "doc-1", "chunkIndex": 2},
#                 ],
#                 "endpoints": [],
#                 "attributes": {},
#             }
#         ]
#     }
#
#     # extract_class_attributes reads objectClassesOutput and documentationItems
#     def mock_get_session_data(session_id, key=None):
#         if key == "objectClassesOutput":
#             return mock_object_classes_output
#         elif key == "documentationItems":
#             return fake_docs
#         return None
#
#     mock_session_manager.get_session_data.side_effect = mock_get_session_data
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await extract_class_attributes(
#             session_id=uuid4(),
#             object_class="User",
#         )
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_class_attributes_status_found():
#     """Test getting class attributes status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {
#             "attributes": {
#                 "id": {
#                     "type": "string",
#                     "description": "Unique identifier",
#                     "mandatory": True,
#                 }
#             }
#         },
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_class_attributes_status(
#             session_id=uuid4(),
#             object_class="User",
#         )
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result is not None
#         assert "id" in response.result.attributes
#         assert response.result.attributes["id"].type == "string"
#
#
# @pytest.mark.asyncio
# async def test_override_class_attributes_success():
#     """Test manual override of class attributes."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     with patch("src.modules.digester.router.SessionManager", mock_session_manager):
#         response = await override_class_attributes(
#             session_id="test-session-id",
#             object_class="User",
#             attributes={"id": {"type": "string"}},
#         )
#
#         # Should write override into session
#         mock_session_manager.update_session.assert_called_once_with(
#             "test-session-id",
#             {"UserAttributesOutput": {"id": {"type": "string"}}},
#         )
#
#         assert response["message"].startswith("Attributes for User overridden successfully")
#         assert response["sessionId"] == "test-session-id"
#         assert response["objectClass"] == "User"
#
#
# # CLASS ENDPOINTS
# @pytest.mark.asyncio
# async def test_extract_class_endpoints_success():
#     """Test successful extraction of endpoints for object class."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]
#
#     # Mock objectClassesOutput with relevant chunks for the User class
#     mock_object_classes_output = {
#         "objectClasses": [
#             {
#                 "name": "User",
#                 "relevant": "true",
#                 "superclass": "",
#                 "abstract": False,
#                 "embedded": False,
#                 "description": "Represents a user",
#                 "relevantChunks": [
#                     {"docUuid": "doc-1", "chunkIndex": 0},
#                     {"docUuid": "doc-1", "chunkIndex": 2},
#                 ],
#                 "endpoints": [],
#             }
#         ]
#     }
#
#     # Mock metadataOutput for baseApiUrl
#     mock_metadata_output = {"infoAboutSchema": {"baseApiEndpoint": [{"uri": "https://api.example.com"}]}}
#
#     # extract_class_endpoints reads objectClassesOutput, metadataOutput, and documentationItems
#     def mock_get_session_data(session_id, key=None):
#         if key == "objectClassesOutput":
#             return mock_object_classes_output
#         elif key == "metadataOutput":
#             return mock_metadata_output
#         elif key == "documentationItems":
#             return fake_docs
#         return None
#
#     mock_session_manager.get_session_data.side_effect = mock_get_session_data
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await extract_class_endpoints(
#             session_id="test-session-id",
#             object_class="User",
#         )
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_class_endpoints_status_found():
#     """Test getting endpoints extraction status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {
#             "endpoints": [
#                 {"method": "GET", "path": "/users", "description": "List users"},
#             ]
#         },
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_class_endpoints_status(
#             session_id=uuid4(),
#             object_class="User",
#         )
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result is not None
#         assert len(response.result.endpoints) == 1
#         assert response.result.endpoints[0].method == "GET"
#         assert response.result.endpoints[0].path == "/users"
#
#
# @pytest.mark.asyncio
# async def test_override_class_endpoints_success():
#     """Test manual override of endpoints."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     with patch("src.modules.digester.router.SessionManager", mock_session_manager):
#         response = await override_class_endpoints(
#             session_id="test-session-id",
#             object_class="User",
#             endpoints={"listUsers": {"method": "GET", "path": "/users"}},
#         )
#
#         mock_session_manager.update_session.assert_called_once_with(
#             "test-session-id",
#             {"UserEndpointsOutput": {"listUsers": {"method": "GET", "path": "/users"}}},
#         )
#
#         assert response["message"].startswith("Endpoints for User overridden successfully")
#         assert response["sessionId"] == "test-session-id"
#         assert response["objectClass"] == "User"
#
#
# # RELATIONS
# @pytest.mark.asyncio
# async def test_extract_relations_success():
#     """Test extracting relations, given that relevant object classes exist in session."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]
#
#     # This endpoint checks session for "objectClassesOutput" first.
#     def fake_get_session_data(session_id, key=None):
#         if key == "objectClassesOutput":
#             return [{"name": "User", "relevant": "true"}]
#         return fake_docs
#
#     mock_session_manager.get_session_data.side_effect = fake_get_session_data
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.common.chunk_filter.filter.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await extract_relations(
#             session_id=uuid4(),
#         )
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_extract_relations_no_classes():
#     """If there are no relevant object classes in the session, it should raise 404."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     def fake_get_session_data(session_id, key=None):
#         if key == "objectClassesOutput":
#             return None
#         return []
#
#     mock_session_manager.get_session_data.side_effect = fake_get_session_data
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content"}]
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.modules.digester.router.filter_documentation_items",
#             return_value=fake_docs,
#         ),
#     ):
#         with pytest.raises(HTTPException) as exc_info:
#             await extract_relations(session_id="test-session-id")
#
#         assert exc_info.value.status_code == 404
#         assert "no object classes" in exc_info.value.detail.lower()
#
#
# @pytest.mark.asyncio
# async def test_get_relations_status_found():
#     """Test getting relations extraction status."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {"relations": [{"from": "User", "to": "Group", "type": "membership"}]},
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_relations_status(
#             session_id=uuid4(),
#         )
#
#         assert response.jobId == job_id
#         assert response.status in (JobStatus.finished, JobStatus.failed)
#
#
# @pytest.mark.asyncio
# async def test_override_relations_success():
#     """Test manual override of relations."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     relations_payload = {"relations": [{"from": "User", "to": "Group", "type": "membership"}]}
#
#     with patch("src.modules.digester.router.SessionManager", mock_session_manager):
#         response = await override_relations(
#             session_id="test-session-id",
#             relations=relations_payload,
#         )
#
#         mock_session_manager.update_session.assert_called_once_with(
#             "test-session-id",
#             {"relationsOutput": relations_payload},
#         )
#
#         assert response["message"].startswith("Relations overridden successfully")
#         assert response["sessionId"] == "test-session-id"
#
#
# # AUTH
# @pytest.mark.asyncio
# async def test_extract_auth_success():
#     """Test extracting auth info."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]
#     mock_session_manager.get_session_data.return_value = fake_docs
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.common.chunk_filter.filter.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await extract_auth(session_id=uuid4())
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_auth_status_found():
#     """Test getting auth extraction status."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {"auth": [{"name": "OAuth2", "type": "authorization_code"}]},
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_auth_status(session_id=uuid4())
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result is not None
#         assert len(response.result.auth) == 1
#         assert response.result.auth[0].name == "OAuth2"
#
#
# # METADATA
# @pytest.mark.asyncio
# async def test_extract_metadata_success():
#     """Test extracting API metadata."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": "doc-1", "content": "fake content for testing"}]
#     mock_session_manager.get_session_data.return_value = fake_docs
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.common.chunk_filter.filter.SessionManager", mock_session_manager),
#         patch(
#             "src.common.session.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.digester.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await extract_metadata(session_id=uuid4())
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_metadata_status_found():
#     """Test getting metadata extraction status."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     mock_status = {
#         "jobId": job_id,
#         "status": "finished",
#         "result": {"infoAboutSchema": [{"title": "Example API", "version": "1.0.0"}]},
#     }
#
#     with (
#         patch("src.modules.digester.router.SessionManager", mock_session_manager),
#         patch("src.modules.digester.router.get_job_status", return_value=mock_status),
#     ):
#         response = await get_metadata_status(session_id=uuid4())
#
#         assert response.jobId == job_id
#         assert response.status in (JobStatus.finished, JobStatus.failed)
