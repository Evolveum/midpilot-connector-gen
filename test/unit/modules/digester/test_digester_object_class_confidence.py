# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.aggregation.object_class_ranking import (
    deduplicate_and_sort_object_classes,
    deduplicate_and_sort_sql_object_classes,
)
from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.schemas import (
    ExtendedObjectClass,
    ObjectClassConfidenceAssignment,
    ObjectClassConfidenceAssignmentsResponse,
    ObjectClassesConfidenceResponse,
    ObjectClassesRankedResponse,
    ObjectClassNameOrderResponse,
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
                ObjectClassWithConfidence(name="Role", description="Role entity", confidence=ConfidenceLevel.HIGH),
                ObjectClassWithConfidence(name="User", description="User entity", confidence=ConfidenceLevel.MEDIUM),
                ObjectClassWithConfidence(
                    name="Permission", description="Permission entity", confidence=ConfidenceLevel.LOW
                ),
            ]
        )
    )

    with (
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch("src.modules.digester.aggregation.object_class_ranking.make_basic_chain", return_value=mock_chain),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert [item.name for item in result.objectClasses] == ["Role", "User", "Permission"]
    assert [item.confidence for item in result.objectClasses] == [
        ConfidenceLevel.HIGH,
        ConfidenceLevel.MEDIUM,
        ConfidenceLevel.LOW,
    ]


@pytest.mark.asyncio
async def test_deduplicate_and_sort_object_classes_defaults_to_low_when_confidence_fails():
    all_object_classes = [
        ExtendedObjectClass(name="User", description="User entity"),
        ExtendedObjectClass(name="Group", description="Group entity"),
    ]

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    with (
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch("src.modules.digester.aggregation.object_class_ranking.make_basic_chain", return_value=mock_chain),
        patch("src.modules.digester.aggregation.object_class_ranking.append_job_error"),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert len(result.objectClasses) == 2
    assert [item.confidence for item in result.objectClasses] == [ConfidenceLevel.LOW, ConfidenceLevel.LOW]


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
                ObjectClassWithConfidence(
                    name="Account", description="Account entity", confidence=ConfidenceLevel.HIGH
                ),
                ObjectClassWithConfidence(name="User", description="User entity", confidence=ConfidenceLevel.HIGH),
                ObjectClassWithConfidence(name="Group", description="Group entity", confidence=ConfidenceLevel.MEDIUM),
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
                    relevant=RelevantLevel.TRUE,
                    confidence=ConfidenceLevel.HIGH,
                ),
                RankedObjectClass(
                    name="Account",
                    description="Account entity",
                    superclass=None,
                    abstract=None,
                    embedded=None,
                    relevant=RelevantLevel.TRUE,
                    confidence=ConfidenceLevel.HIGH,
                ),
            ]
        )
    )

    with (
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.make_basic_chain", return_value=classification_chain
        ),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.build_structured_chain", return_value=sorting_chain
        ),
    ):
        result = await deduplicate_and_sort_object_classes(
            all_object_classes=all_object_classes,
            job_id=uuid4(),
        )

    assert [item.name for item in result.objectClasses] == ["User", "Account", "Group"]
    assert [item.confidence for item in result.objectClasses] == [
        ConfidenceLevel.HIGH,
        ConfidenceLevel.HIGH,
        ConfidenceLevel.MEDIUM,
    ]


@pytest.mark.asyncio
async def test_sql_ranking_uses_compact_payloads_and_restores_full_objects():
    all_object_classes = [
        ExtendedObjectClass(
            name="m_assignment",
            description="Full assignment table description with every final-response detail.",
            superclass="m_container",
            abstract=False,
            embedded=True,
        ),
        ExtendedObjectClass(
            name="m_user",
            description="Full user table description with every final-response detail.",
            superclass="m_focus",
            abstract=False,
            embedded=False,
        ),
    ]
    ranking_descriptions = {
        "m_assignment": "Table m_assignment; columns: owneroid, targetoid.",
        "m_user": "Table m_user; columns: oid, nameorig.",
    }

    confidence_chain = MagicMock()
    confidence_chain.ainvoke = AsyncMock(
        return_value=ObjectClassConfidenceAssignmentsResponse(
            object_classes=[
                ObjectClassConfidenceAssignment(name="m_assignment", confidence=ConfidenceLevel.HIGH),
                ObjectClassConfidenceAssignment(name="m_user", confidence=ConfidenceLevel.HIGH),
            ]
        )
    )
    sorting_chain = MagicMock()
    sorting_chain.ainvoke = AsyncMock(
        return_value=ObjectClassNameOrderResponse(object_classes=["m_user", "m_assignment"])
    )

    with (
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.make_basic_chain",
            return_value=confidence_chain,
        ) as build_confidence_chain,
        patch(
            "src.modules.digester.aggregation.object_class_ranking.build_structured_chain",
            return_value=sorting_chain,
        ) as build_sorting_chain,
    ):
        result = await deduplicate_and_sort_sql_object_classes(
            all_object_classes,
            uuid4(),
            ranking_descriptions=ranking_descriptions,
        )

    confidence_prompt = build_confidence_chain.call_args.kwargs["prompt"]
    confidence_messages = confidence_prompt.format_messages()
    confidence_input = confidence_messages[1].content
    assert ranking_descriptions["m_assignment"] in confidence_input
    assert ranking_descriptions["m_user"] in confidence_input
    assert "Full assignment table description" not in confidence_input
    assert "Full user table description" not in confidence_input
    confidence_schema = build_confidence_chain.call_args.kwargs["parser"].pydantic_object.model_json_schema()
    assignment_properties = confidence_schema["$defs"]["ObjectClassConfidenceAssignment"]["properties"]
    assert set(assignment_properties) == {"name", "confidence"}

    assert build_sorting_chain.call_args.args[2] is ObjectClassNameOrderResponse
    sorting_input = json.loads(sorting_chain.ainvoke.await_args.args[0]["items_json"])
    assert sorting_input == [
        {"name": "m_assignment", "description": ranking_descriptions["m_assignment"]},
        {"name": "m_user", "description": ranking_descriptions["m_user"]},
    ]

    assert [item.name for item in result.objectClasses] == ["m_user", "m_assignment"]
    assert result.objectClasses[0].description == "Full user table description with every final-response detail."
    assert result.objectClasses[0].superclass == "m_focus"
    assert result.objectClasses[0].embedded is False
    assert result.objectClasses[1].description == (
        "Full assignment table description with every final-response detail."
    )
    assert result.objectClasses[1].superclass == "m_container"
    assert result.objectClasses[1].embedded is True
