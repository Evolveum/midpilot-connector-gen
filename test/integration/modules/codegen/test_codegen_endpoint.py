#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

# """Integration tests for codegen endpoints."""
#
# from unittest.mock import AsyncMock, MagicMock, patch
# from uuid import uuid4
#
# import pytest
# from fastapi import HTTPException
#
# from src.common.enums import JobStatus
# from src.modules.codegen.router import (
#     generate_connid,
#     generate_native_schema,
#     generate_relation_code,
#     generate_search,
#     get_native_schema_status,
#     get_relation_code_status,
#     override_native_schema,
# )
#
#
# # NATIVE SCHEMA
# @pytest.mark.asyncio
# async def test_generate_native_schema_success():
#     """Test successful generation of native schema."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.get_session_data.return_value = {"username": {"type": "string"}}
#     mock_session_manager.update_session = MagicMock()
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.modules.codegen.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await generate_native_schema(session_id, "User")
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_native_schema_status_found():
#     """Test getting native schema generation status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.modules.codegen.router.build_stage_status_response") as mock_builder,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_builder.return_value = MagicMock(
#             jobId=job_id,
#             status=JobStatus.finished,
#             result={"code": "mocked groovy code"},
#             progress={"stage": "queued"},
#             errors=None,
#         )
#
#         response = await get_native_schema_status(
#             session_id,
#             "User",
#             job_id,
#         )
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result == {"code": "mocked groovy code"}
#         assert response.progress == {"stage": "queued"}
#         assert response.errors is None
#
#
# @pytest.mark.asyncio
# async def test_override_native_schema_success():
#     """Test manual override of native schema."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     with patch("src.modules.codegen.router.SessionManager", mock_session_manager):
#         session_id = uuid4()
#         response = await override_native_schema(
#             session_id,
#             "User",
#             {"code": "custom groovy code"},
#         )
#
#         assert response["message"] == "Native schema for User overridden successfully"
#         assert response["sessionId"] == session_id
#         assert response["objectClass"] == "User"
#
#         mock_session_manager.update_session.assert_called_once_with(
#             session_id,
#             {"UserNativeSchema": {"code": "custom groovy code"}},
#         )
#
#
# # CONNID
# @pytest.mark.asyncio
# async def test_generate_connid_success():
#     """Test successful generation of ConnID code."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.get_session_data.return_value = {"username": {"type": "string"}}
#     mock_session_manager.update_session = MagicMock()
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.modules.codegen.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await generate_connid(session_id, "User")
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# # SEARCH
# @pytest.mark.asyncio
# async def test_generate_search_success():
#     """Test successful generation of search code."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     # emulate SessionManager.get_session_data(...) returning different values based on key:
#     def fake_get_session_data(session_id, key):
#         if key.endswith("AttributesOutput"):
#             return {"username": {"type": "string"}}
#         if key.endswith("EndpointsOutput"):
#             return {"endpoints": [{"method": "GET", "path": "/users"}]}
#         return None
#
#     mock_session_manager.get_session_data.side_effect = fake_get_session_data
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": str(uuid4()), "content": "fake content from docs"}]
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.modules.codegen.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.codegen.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await generate_search(session_id, "User", "GET")
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# # RELATION
# @pytest.mark.asyncio
# async def test_generate_relation_code_success():
#     """Test successful generation of relation code."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     def fake_get_session_data(session_id, key):
#         if key == "relationsOutput":
#             return {"relations": [{"subject": "User", "object": "Group", "type": "membership"}]}
#         return None
#
#     mock_session_manager.get_session_data.side_effect = fake_get_session_data
#     mock_session_manager.update_session = MagicMock()
#
#     fake_docs = [{"uuid": str(uuid4()), "content": "fake relations doc"}]
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch(
#             "src.modules.codegen.router.get_session_documentation",
#             new=AsyncMock(return_value=fake_docs),
#         ),
#         patch("src.modules.codegen.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         response = await generate_relation_code(session_id, "membership")
#
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_get_relation_code_status_found():
#     """Test getting relation code generation status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#         patch("src.modules.codegen.router._build_multi_doc_status_response") as mock_builder,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_builder.return_value = MagicMock(
#             jobId=job_id,
#             status=JobStatus.finished,
#             result="mocked relation code",
#             progress=None,
#             errors=None,
#         )
#
#         response = await get_relation_code_status(
#             session_id,
#             "membership",
#             job_id,
#         )
#
#         assert response.jobId == job_id
#         assert response.status == JobStatus.finished
#         assert response.result == "mocked relation code"
#         assert response.progress is None
#         assert response.errors is None
#
#
# # ERROR HANDLING
# @pytest.mark.asyncio
# async def test_generate_native_schema_missing_class():
#     """If attributes for the class don't exist in session, raise 404."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.get_session_data.return_value = None
#
#     with (
#         patch("src.modules.codegen.router.SessionManager", mock_session_manager),
#         patch("src.common.session.router.SessionManager", mock_session_manager),
#     ):
#         with pytest.raises(HTTPException) as exc_info:
#             await generate_native_schema("test-session", "NonExistentClass")
#
#     assert exc_info.value.status_code == 404
#     assert "No attributes found for NonExistentClass" in exc_info.value.detail
