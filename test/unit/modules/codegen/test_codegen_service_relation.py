# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service relation generator."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.codegen import service
from src.modules.digester.schema import RelationsResponse


@pytest.mark.asyncio
async def test_generate_relation():
    """Test generating relation code."""
    test_relations_payload = {
        "relations": [
            {
                "name": "project_to_membership",
                "displayName": "Project to Membership",
                "subject": "project",
                "object": "membership",
                "subjectAttribute": "memberships",
                "objectAttribute": "",
                "shortDescription": "",
            },
            {
                "name": "membership_to_principal",
                "displayName": "Membership to Principal",
                "subject": "membership",
                "object": "principal",
                "subjectAttribute": "principal",
                "objectAttribute": "",
                "shortDescription": "",
            },
        ]
    }

    with (
        patch("src.modules.codegen.service.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.service.SessionRepository") as mock_session_repository,
        patch("src.modules.codegen.service.RelationGenerator") as mock_relation_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_session_repository.return_value
        mock_repo_instance.get_session_data = AsyncMock(return_value=None)

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_relation_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked relation code")

        relations_model = RelationsResponse.model_validate(test_relations_payload)

        result = await service.create_relation(
            relations=relations_model,
            session_id=uuid4(),
            job_id=uuid4(),
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked relation code"

        # Verify generator was instantiated and generate method was called
        mock_relation_generator_class.assert_called_once()
        mock_generator_instance.generate.assert_called_once()
