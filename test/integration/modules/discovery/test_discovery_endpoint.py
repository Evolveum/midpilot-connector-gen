#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

# """Integration tests for discovery endpoints."""
#
# from types import SimpleNamespace
# from unittest.mock import MagicMock, patch
# from uuid import uuid4
#
# import pytest
# from fastapi import HTTPException
#
# from src.modules.discovery.router import discover_candidate_links, get_discovery_status
# from src.modules.discovery.schema import CandidateLinksInput
#
#
# @pytest.mark.asyncio
# async def test_discover_candidate_links_success():
#     """Test successful discovery of candidate links."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.update_session = MagicMock()
#
#     with (
#         patch("src.modules.discovery.router.SessionManager", mock_session_manager),
#         patch("src.modules.discovery.router.schedule_coroutine_job") as mock_schedule,
#     ):
#         job_id = uuid4()
#         session_id = uuid4()
#         mock_schedule.return_value = job_id
#
#         request = CandidateLinksInput(application_name="test-app")
#         response = await discover_candidate_links(request, session_id)
#
#         # NOTE: response model is camelCase
#         assert response.jobId == job_id
#         mock_schedule.assert_called_once()
#         mock_session_manager.update_session.assert_called_once()
#
#
# @pytest.mark.asyncio
# async def test_discover_candidate_links_session_not_found():
#     """Test discovery with non-existent session."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = False
#
#     with patch("src.modules.discovery.router.SessionManager", mock_session_manager):
#         request = CandidateLinksInput(application_name="test-app")
#         with pytest.raises(HTTPException) as exc_info:
#             await discover_candidate_links(request, uuid4())
#
#         assert exc_info.value.status_code == 404
#         assert "not found" in str(exc_info.value.detail).lower()
#
#
# @pytest.mark.asyncio
# async def test_get_discovery_status_found():
#     """Test getting discovery status when job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     job_id = uuid4()
#     mock_session_manager.get_session_data.return_value = str(job_id)
#
#     # Router calls build_stage_status_response, so patch that
#     fake_status = SimpleNamespace(jobId=job_id, status="completed", result={"candidateLinks": ["https://example.com"]})
#
#     with (
#         patch("src.modules.discovery.router.SessionManager", mock_session_manager),
#         patch("src.modules.discovery.router.build_stage_status_response", return_value=fake_status),
#     ):
#         response = await get_discovery_status(uuid4())
#
#         assert response.jobId == job_id
#         assert response.status == "completed"
#         assert "candidateLinks" in response.result
#
#
# @pytest.mark.asyncio
# async def test_get_discovery_status_not_found():
#     """Test getting discovery status when no job exists."""
#     mock_session_manager = MagicMock()
#     mock_session_manager.session_exists.return_value = True
#     mock_session_manager.get_session_data.return_value = None
#
#     with patch("src.modules.discovery.router.SessionManager", mock_session_manager):
#         # Pass jobId=None explicitly; otherwise a Query(None) object flows through
#         with pytest.raises(HTTPException) as exc_info:
#             await get_discovery_status(uuid4(), jobId=None)
#
#         assert exc_info.value.status_code == 404
#         assert "no discovery job" in str(exc_info.value.detail).lower()
