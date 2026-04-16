# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.extractors.rest.object_class import deduplicate_and_sort_object_classes
from src.modules.digester.schema import (
    ExtendedObjectClass,
    ObjectClassesConfidenceResponse,
    ObjectClassesRankedResponse,
    ObjectClassWithConfidence,
    RankedObjectClass,
)


@pytest.mark.asyncio
async def test_deduplicate_and_sort_object_classes_keeps_all_and_sorts_by_confidence():
    all_object_classes = [
        ExtendedObjectClass(name="Role", description="Role entity"),
        ExtendedObjectClass(name="User", description="User entity"),
        ExtendedObjectClass(name="Permission", description="Permission entity"),
    ]

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(
        return_value=ObjectClassesConfidenceResponse(
            object_classes=[
                ObjectClassWithConfidence(name="Role", description="Role entity", confidence="high"),
                ObjectClassWithConfidence(name="User", description="User entity", confidence="medium"),
                ObjectClassWithConfidence(name="Permission", description="Permission entity", confidence="low"),
            ]
        )
    )

    with (
        patch("src.modules.digester.extractors.rest.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.rest.object_class.get_default_llm", return_value=MagicMock()),
        patch("src.modules.digester.extractors.rest.object_class.make_basic_chain", return_value=mock_chain),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert [item.name for item in result.objectClasses] == ["Role", "User", "Permission"]
    assert [item.confidence for item in result.objectClasses] == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_deduplicate_and_sort_object_classes_defaults_to_low_when_confidence_fails():
    all_object_classes = [
        ExtendedObjectClass(name="User", description="User entity"),
        ExtendedObjectClass(name="Group", description="Group entity"),
    ]

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    with (
        patch("src.modules.digester.extractors.rest.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.rest.object_class.get_default_llm", return_value=MagicMock()),
        patch("src.modules.digester.extractors.rest.object_class.make_basic_chain", return_value=mock_chain),
        patch("src.modules.digester.extractors.rest.object_class.append_job_error"),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert len(result.objectClasses) == 2
    assert [item.confidence for item in result.objectClasses] == ["low", "low"]


@pytest.mark.asyncio
async def test_deduplicate_and_sort_object_classes_sorts_with_llm_inside_same_confidence():
    all_object_classes = [
        ExtendedObjectClass(name="Account", description="Account entity"),
        ExtendedObjectClass(name="User", description="User entity"),
        ExtendedObjectClass(name="Group", description="Group entity"),
    ]

    classification_chain = MagicMock()
    classification_chain.ainvoke = AsyncMock(
        return_value=ObjectClassesConfidenceResponse(
            object_classes=[
                ObjectClassWithConfidence(name="Account", description="Account entity", confidence="high"),
                ObjectClassWithConfidence(name="User", description="User entity", confidence="high"),
                ObjectClassWithConfidence(name="Group", description="Group entity", confidence="medium"),
            ]
        )
    )

    sorting_chain = MagicMock()
    sorting_chain.ainvoke = AsyncMock(
        return_value=ObjectClassesRankedResponse(
            object_classes=[
                RankedObjectClass(
                    name="User",
                    description="User entity",
                    superclass=None,
                    abstract=None,
                    embedded=None,
                    relevant="true",
                    confidence="high",
                ),
                RankedObjectClass(
                    name="Account",
                    description="Account entity",
                    superclass=None,
                    abstract=None,
                    embedded=None,
                    relevant="true",
                    confidence="high",
                ),
            ]
        )
    )

    with (
        patch("src.modules.digester.extractors.rest.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extractors.rest.object_class.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.extractors.rest.object_class.make_basic_chain",
            side_effect=[classification_chain, sorting_chain],
        ),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert [item.name for item in result.objectClasses] == ["User", "Account", "Group"]
    assert [item.confidence for item in result.objectClasses] == ["high", "high", "medium"]
