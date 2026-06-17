# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.schemas import AttributeInfoRest


# ==================== EXTRACT ATTRIBUTES ====================
@pytest.mark.asyncio
async def test_extract_attributes_updates_session_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extract_attributes successfully extracts attributes and updates the session.
    Validates chunk selection, attribute extraction, and session update.
    """
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())

    fake_doc_items = [
        {
            "uuid": doc_uuid,
            "content": "User schema documentation",
            "summary": "User attributes",
            "@metadata": {"source": "schema"},
        }
    ]

    relevant_chunks = [
        {"doc_id": doc_uuid, "chunk_id": doc_uuid},
    ]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_attributes") as mock_extract_attrs,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (
            ["chunk-0 text", "chunk-2 text"],
            [(0, doc_uuid), (2, doc_uuid)],
        )

        mock_extract_attrs.return_value = {
            "result": {
                "attributes": {
                    "id": AttributeInfoRest(
                        type="string",
                        description="Unique identifier",
                        mandatory=True,
                        readable=True,
                        updatable=False,
                        creatable=True,
                        multivalue=False,
                        returnedByDefault=True,
                        relevant_sequences=[],
                    ).model_dump(),
                    "username": AttributeInfoRest(
                        type="string",
                        description="User login name",
                        mandatory=True,
                        readable=True,
                        updatable=True,
                        creatable=True,
                        multivalue=False,
                        returnedByDefault=True,
                        relevant_sequences=[],
                    ).model_dump(),
                }
            },
            "relevantDocumentations": relevant_chunks,
        }

        result = await service.extract_attributes(fake_doc_items, "User", session_id, relevant_chunks, job_id)

        # Verify result structure
        assert "result" in result
        assert "attributes" in result["result"]
        assert "id" in result["result"]["attributes"]
        assert "username" in result["result"]["attributes"]

        # Verify chunk extraction was called correctly
        mock_extract_chunks.assert_called_once_with(fake_doc_items, relevant_chunks, "Digester:Attributes")

        # Verify attribute extraction was called
        mock_extract_attrs.assert_called_once()
        mock_update_object_class.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_attributes_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch(
            "src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]
        ) as mock_api_types,
    ):
        result = await service.extract_attributes([], "User", session_id, [], job_id)

        assert result["result"]["attributes"] == {}
        assert result["relevantDocumentations"] == []
        mock_extract_chunks.assert_not_called()
        mock_api_types.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_extract_attributes_scim_preserves_doc_maps_when_relevance_is_empty(
    mock_llm, mock_digester_update_job_progress
):
    session_id = uuid4()
    job_id = uuid4()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    doc_items = [
        {
            "docId": doc_id,
            "chunkId": chunk_id,
            "content": "Slack maps primary email to emails[0].value.",
            "summary": "SCIM user email mappings",
            "@metadata": {"tags": ["scim", "attributes"]},
        }
    ]
    retry_result = {
        "result": {
            "attributes": {
                "Primary Email": {
                    "type": "string",
                    "format": "email",
                    "description": "Primary email mapping.",
                    "mandatory": True,
                    "scimAttribute": "emails.value",
                    "relevantDocumentations": [{"docId": doc_id, "chunkId": chunk_id}],
                }
            }
        },
        "relevantDocumentations": [{"doc_id": doc_id, "chunk_id": chunk_id}],
    }

    with (
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=["SCIM"]),
        patch(
            "src.modules.digester.service.filter_documentation_items", new_callable=AsyncMock, return_value=doc_items
        ),
        patch(
            "src.modules.digester.service.extract_scim_attributes",
            new_callable=AsyncMock,
            side_effect=[
                {"result": {"attributes": {}}, "relevantDocumentations": []},
                retry_result,
            ],
        ) as mock_extract_scim_attributes,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
    ):
        result = await service.extract_attributes(doc_items, "UserEmails", session_id, [], job_id)

    assert result == retry_result
    assert mock_extract_scim_attributes.await_count == 2

    first_call_args = mock_extract_scim_attributes.await_args_list[0].args
    assert first_call_args[0] == []
    assert first_call_args[3] == []
    assert first_call_args[5] == {chunk_id: doc_id}

    retry_call_args = mock_extract_scim_attributes.await_args_list[1].args
    assert retry_call_args[0] == ["Slack maps primary email to emails[0].value."]
    assert retry_call_args[3] == [chunk_id]
    assert retry_call_args[4] == {
        chunk_id: {
            "summary": "SCIM user email mappings",
            "@metadata": {"tags": ["scim", "attributes"]},
        }
    }
    assert retry_call_args[5] == {chunk_id: doc_id}
    mock_update_object_class.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_attributes_session_not_found(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes handles missing session gracefully."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())

    fake_doc_items = [{"uuid": doc_uuid, "content": "test"}]
    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_attributes") as mock_extract_attrs,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk text"], [(0, doc_uuid)])
        mock_extract_attrs.return_value = {"result": {"attributes": {"id": {}}}, "relevantDocumentations": []}

        result = await service.extract_attributes(fake_doc_items, "User", session_id, relevant_chunks, job_id)

        # Should return result even if session update fails
        assert "result" in result
        mock_update_object_class.assert_awaited_once()
