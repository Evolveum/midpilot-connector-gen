# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch
from uuid import uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.schema import RelationRecord


# ==================== EXTRACT RELATIONS ====================
@pytest.mark.asyncio
async def test_extract_relations_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extracting relations between object classes.
    Validates parallel processing and relation merging.
    """
    doc_uuid = uuid4()
    fake_doc_items = [
        {
            "uuid": str(doc_uuid),
            "content": "User-Group relationship documentation",
            "summary": "Relations",
            "@metadata": {"tags": "relations"},
        }
    ]

    relevant_object_class = "User"

    with (
        patch("src.modules.digester.service._extract_relations"),
        patch("src.modules.digester.service.merge_relations_results"),
        patch("src.modules.digester.service._process_over_chunks") as mock_process,
    ):
        mock_process.return_value = {
            "result": {
                "relations": [
                    RelationRecord(
                        name="user_groups",
                        short_description="User membership in groups",
                        subject="user",
                        subject_attribute="groups",
                        object="group",
                        object_attribute="members",
                    ).model_dump(by_alias=True)
                ]
            },
            "relevantDocumentations": [{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}],
        }

        job_id = uuid4()
        result = await service.extract_relations(fake_doc_items, relevant_object_class, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "relations" in result["result"]

        relation = result["result"]["relations"][0]
        assert relation["subject"] == "user"
        assert relation["object"] == "group"


@pytest.mark.asyncio
async def test_extract_relations_no_relations_found(mock_llm, mock_digester_update_job_progress):
    """Test extract_relations when no relations are discovered."""
    fake_doc_items = [{"uuid": str(uuid4()), "content": "No relations", "summary": "", "@metadata": {}}]

    with patch("src.modules.digester.service._process_over_chunks") as mock_process:
        mock_process.return_value = {"result": {"relations": []}, "relevantDocumentations": []}

        result = await service.extract_relations(fake_doc_items, "User", uuid4())

        assert result["result"]["relations"] == []
