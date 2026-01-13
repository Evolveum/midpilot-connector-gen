#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

# from unittest.mock import MagicMock, patch
# from uuid import UUID, uuid4
#
# import pytest
#
# from src.modules.digester import service
# from src.modules.digester.schema import ObjectClass
#
#
# # extract_object_classes
# @pytest.mark.asyncio
# async def test_extract_object_classes(mock_llm, mock_digester_update_job_progress):
#     """
#     Test extracting object classes from multiple documentation items.
#     Matches the new async, multi-doc, progress-reporting version.
#     """
#
#     fake_doc_items = [{"uuid": str(uuid4()), "content": "Test documentation content"}]
#
#     with (
#         patch("src.modules.digester.service.extract_object_classes_raw") as mock_raw,
#         patch("src.modules.digester.service.deduplicate_and_sort_object_classes") as mock_dedupe,
#         patch("src.modules.digester.utils.parallel_docs.update_job_progress") as mock_parallel_progress,
#     ):
#         mock_raw.return_value = (
#             [
#                 ObjectClass(
#                     name="User",
#                     relevant="true",
#                     superclass="",
#                     abstract=False,
#                     embedded=False,
#                     description="User's description",
#                     relevant_chunks=[{"docUuid": uuid4(), "chunkIndex": 0}],
#                 ),
#                 ObjectClass(
#                     name="Group",
#                     relevant="true",
#                     superclass="",
#                     abstract=False,
#                     embedded=False,
#                     description="Group's description",
#                     relevant_chunks=[{"docUuid": uuid4(), "chunkIndex": 1}],
#                 ),
#             ],
#             [0, 1],
#         )
#
#         class FakeDeduped:
#             def model_dump(self, by_alias=True):
#                 return {
#                     "objectClasses": [
#                         {"name": "User", "relevant": "true"},
#                         {"name": "Group", "relevant": "true"},
#                     ]
#                 }
#
#         mock_dedupe.return_value = FakeDeduped()
#
#         result = await service.extract_object_classes(
#             fake_doc_items,
#             True,
#             "high",
#             uuid4(),
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#
#         assert "objectClasses" in result["result"]
#         assert len(result["result"]["objectClasses"]) == 2
#         assert result["result"]["objectClasses"][0]["name"] == "User"
#         assert result["result"]["objectClasses"][1]["name"] == "Group"
#
#         # Verify progress tracking was called
#         assert mock_parallel_progress.called or mock_digester_update_job_progress.called
#
#
# # extract attributes
# @pytest.mark.asyncio
# async def test_extract_attributes_updates_session(mock_llm, mock_digester_update_job_progress):
#     """
#     service.extract_attributes now:
#     - takes doc_items + object_class + session_id + relevant_chunks + job_id
#     - extracts only selected chunks
#     - calls lower-level _extract_attributes
#     - writes the attributes back into the objectClassesOutput in session
#     """
#
#     doc_uuid = str(uuid4())
#     fake_doc_items = [{"uuid": doc_uuid, "content": "Test documentation content"}]
#     relevant_chunks = [
#         {"docUuid": doc_uuid, "chunkIndex": 0},
#         {"docUuid": doc_uuid, "chunkIndex": 2},
#     ]
#
#     object_classes_output = {
#         "objectClasses": [
#             {
#                 "name": "User",
#                 "relevant": "true",
#                 "superclass": "",
#                 "abstract": False,
#                 "embedded": False,
#                 "relevantChunks": relevant_chunks,
#             }
#         ]
#     }
#
#     mock_session = MagicMock()
#     mock_session.get_session_data.return_value = object_classes_output
#
#     with (
#         patch("src.modules.digester.service.SessionManager", mock_session),
#         patch("src.modules.digester.service._extract_specific_chunks") as mock_extract_specific,
#         patch("src.modules.digester.service._extract_attributes") as mock_low_level_attrs,
#     ):
#         mock_extract_specific.return_value = (
#             ["chunk-0 text", "chunk-2 text"],
#             [(0, doc_uuid), (2, doc_uuid)],
#         )
#
#         mock_low_level_attrs.return_value = {
#             "result": {
#                 "attributes": {
#                     "id": {"type": "string", "description": "Unique identifier"},
#                     "username": {"type": "string", "description": "User login"},
#                 }
#             },
#             "relevantChunks": [
#                 {"docUuid": doc_uuid, "chunkIndex": 0},
#                 {"docUuid": doc_uuid, "chunkIndex": 2},
#             ],
#         }
#
#         result = await service.extract_attributes(
#             doc_items=fake_doc_items,
#             object_class="User",
#             session_id=uuid4(),
#             relevant_chunks=relevant_chunks,
#             job_id=uuid4(),
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#         assert "attributes" in result["result"]
#         assert "id" in result["result"]["attributes"]
#         assert "username" in result["result"]["attributes"]
#
#         mock_session.update_session.assert_called_once()
#         args, kwargs = mock_session.update_session.call_args
#         # args[0] is the UUID session_id passed above
#         assert args[0] is not None
#         updated_payload = args[1]
#         assert "objectClassesOutput" in updated_payload
#         updated_obj_classes = updated_payload["objectClassesOutput"]["objectClasses"]
#         assert len(updated_obj_classes) == 1
#         assert updated_obj_classes[0]["name"].lower() == "user"
#         assert "attributes" in updated_obj_classes[0]
#         assert "id" in updated_obj_classes[0]["attributes"]
#
#
# # extract endpoints
# @pytest.mark.asyncio
# async def test_extract_endpoints_updates_session(mock_llm, mock_digester_update_job_progress):
#     """
#     service.extract_endpoints now:
#     - takes doc_items + object_class + session_id + relevant_chunks + job_id + base_api_url
#     - extracts only selected chunks
#     - calls lower-level _extract_endpoints
#     - writes the endpoints back into the objectClassesOutput in session
#     """
#
#     doc_uuid = str(uuid4())
#     fake_doc_items = [{"uuid": doc_uuid, "content": "Test documentation content"}]
#     relevant_chunks = [
#         {"docUuid": doc_uuid, "chunkIndex": 0},
#         {"docUuid": doc_uuid, "chunkIndex": 3},
#     ]
#
#     object_classes_output = {
#         "objectClasses": [
#             {
#                 "name": "User",
#                 "relevant": "true",
#                 "superclass": "",
#                 "abstract": False,
#                 "embedded": False,
#                 "relevantChunks": relevant_chunks,
#                 "attributes": {},
#                 "endpoints": [],
#             }
#         ]
#     }
#
#     mock_session = MagicMock()
#
#     def _get_session_data(session_id, key=None):
#         if key == "objectClassesOutput":
#             return object_classes_output
#         return None
#
#     mock_session.get_session_data.side_effect = _get_session_data
#
#     with (
#         patch("src.modules.digester.service.SessionManager", mock_session),
#         patch("src.modules.digester.service._extract_specific_chunks") as mock_extract_specific,
#         patch("src.modules.digester.service._extract_endpoints") as mock_low_level_endpoints,
#     ):
#         mock_extract_specific.return_value = (
#             ["chunk-0 text", "chunk-3 text"],
#             [(0, doc_uuid), (3, doc_uuid)],
#         )
#
#         mock_low_level_endpoints.return_value = {
#             "result": {
#                 "endpoints": [
#                     {
#                         "method": "GET",
#                         "path": "/users",
#                         "description": "List users",
#                     }
#                 ]
#             },
#             "relevantChunks": [
#                 {"docUuid": "doc-1", "chunkIndex": 0},
#             ],
#         }
#
#         result = await service.extract_endpoints(
#             doc_items=fake_doc_items,
#             object_class="User",
#             session_id=uuid4(),
#             relevant_chunks=relevant_chunks,
#             job_id=uuid4(),
#             base_api_url="https://api.example.com",
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#         assert "endpoints" in result["result"]
#         assert len(result["result"]["endpoints"]) == 1
#         assert result["result"]["endpoints"][0]["path"] == "/users"
#
#         mock_session.update_session.assert_called_once()
#         args, kwargs = mock_session.update_session.call_args
#         assert isinstance(args[0], UUID)
#         updated_payload = args[1]
#         assert "objectClassesOutput" in updated_payload
#         updated_obj_classes = updated_payload["objectClassesOutput"]["objectClasses"]
#         assert len(updated_obj_classes) == 1
#         assert updated_obj_classes[0]["name"].lower() == "user"
#         assert "endpoints" in updated_obj_classes[0]
#         assert len(updated_obj_classes[0]["endpoints"]) == 1
#         assert updated_obj_classes[0]["endpoints"][0]["path"] == "/users"
#
#
# # extract_auth
# @pytest.mark.asyncio
# async def test_extract_auth(mock_llm, mock_digester_update_job_progress):
#     """
#     Test extracting authentication info across multiple documents.
#     """
#
#     fake_doc_items = [{"uuid": str(uuid4()), "content": "Test documentation content"}]
#
#     with (
#         patch("src.modules.digester.service.extract_auth_raw") as mock_extract,
#         patch("src.modules.digester.service.deduplicate_and_sort_auth") as mock_dedupe,
#         patch("src.modules.digester.utils.parallel_docs.update_job_progress") as mock_parallel_progress,
#     ):
#         mock_extract.return_value = (
#             [
#                 {
#                     "name": "OAuth2",
#                     "type": "authorization_code",
#                     "quirks": None,
#                 }
#             ],
#             [0, 1],
#         )
#
#         class FakeDedupedAuth:
#             def model_dump(self, by_alias=True):
#                 return {
#                     "auth": [
#                         {
#                             "name": "OAuth2",
#                             "type": "authorization_code",
#                             "quirks": None,
#                         }
#                     ]
#                 }
#
#         mock_dedupe.return_value = FakeDedupedAuth()
#
#         result = await service.extract_auth(
#             fake_doc_items,
#             uuid4(),
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#
#         assert "auth" in result["result"]
#         assert len(result["result"]["auth"]) == 1
#         assert result["result"]["auth"][0]["name"] == "OAuth2"
#         assert result["result"]["auth"][0]["type"] == "authorization_code"
#
#         # Verify progress tracking was called
#         assert mock_parallel_progress.called or mock_digester_update_job_progress.called
#
#
# # extract_info_metadata
# @pytest.mark.asyncio
# async def test_extract_info_metadata(mock_llm, mock_digester_update_job_progress):
#     """
#     Test extracting API metadata (infoAboutSchema, etc.) across documents.
#     """
#
#     fake_doc_items = [{"uuid": str(uuid4()), "content": "Test documentation content"}]
#
#     with (
#         patch("src.modules.digester.service._extract_info_metadata") as mock_extract,
#         patch("src.modules.digester.service.update_job_progress") as mock_update_progress,
#     ):
#         mock_extract.return_value = (
#             {
#                 "infoAboutSchema": [
#                     {
#                         "title": "Example API",
#                         "version": "1.0.0",
#                     }
#                 ]
#             },
#             [2],
#         )
#
#         result = await service.extract_info_metadata(
#             fake_doc_items,
#             uuid4(),
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#
#         assert "infoAboutSchema" in result["result"]
#         assert len(result["result"]["infoAboutSchema"]) == 1
#         assert result["result"]["infoAboutSchema"][0]["title"] == "Example API"
#
#         assert mock_update_progress.called or mock_digester_update_job_progress.called
#
#
# # extract_relations
# @pytest.mark.asyncio
# async def test_extract_relations(mock_llm, mock_digester_update_job_progress):
#     """
#     Test extracting relations between object classes from multiple docs.
#     """
#
#     fake_doc_items = [{"uuid": str(uuid4()), "content": "Test documentation content"}]
#
#     with (
#         patch("src.modules.digester.service._extract_relations") as mock_extract,
#         patch("src.modules.digester.service.merge_relations_results") as mock_merge,
#         patch("src.modules.digester.utils.parallel_docs.update_job_progress") as mock_parallel_progress,
#     ):
#         mock_extract.return_value = (
#             {
#                 "relations": [
#                     {
#                         "from": "User",
#                         "to": "Group",
#                         "type": "membership",
#                     }
#                 ]
#             },
#             [1, 3],
#         )
#
#         mock_merge.return_value = {
#             "relations": [
#                 {
#                     "from": "User",
#                     "to": "Group",
#                     "type": "membership",
#                 }
#             ]
#         }
#
#         result = await service.extract_relations(
#             fake_doc_items,
#             relevant_object_class="User",
#             job_id=uuid4(),
#         )
#
#         assert "result" in result
#         assert "relevantChunks" in result
#
#         assert "relations" in result["result"]
#         assert len(result["result"]["relations"]) == 1
#         assert result["result"]["relations"][0]["from"] == "User"
#         assert result["result"]["relations"][0]["to"] == "Group"
#
#         # Verify progress tracking was called
#         assert mock_parallel_progress.called or mock_digester_update_job_progress.called
