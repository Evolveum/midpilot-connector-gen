# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.enums import ConfidenceLevel, RelevantLevel
from src.modules.digester.extractors.scim.baseline import build_scim_baseline_bundle, get_base_scim_object_classes
from src.modules.digester.extractors.scim.object_class import (
    build_embedded_object_class_name,
    extract_custom_scim_classes,
    extract_scim_object_classes,
    get_embedded_object_classes_from_scim_schema,
    get_embedded_object_classes_from_scim_schemas,
)
from src.modules.digester.schemas import (
    ExtendedObjectClass,
    ObjectClassesConfidenceResponse,
    ObjectClassesRankedResponse,
    ObjectClassWithConfidence,
    RankedObjectClass,
)
from src.modules.digester.schemas.common import ChunkReference

_SCHEMA_DIR = Path(__file__).parent / "scim_schemas"


def _baseline_schemas() -> dict:
    """Load the SCIM baseline schemas that stand in for a session's conndev documents."""
    schemas: dict = {}
    for path in sorted(_SCHEMA_DIR.glob("*.json")):
        schema = json.loads(path.read_text())
        schemas[schema["name"]] = schema
    return schemas


BASELINE_SCHEMAS = _baseline_schemas()
BASELINE_BUNDLE = build_scim_baseline_bundle(BASELINE_SCHEMAS)


def _confidence_response(bundle, assignments: dict[str, ConfidenceLevel] | None = None):
    assignments = assignments or {}
    candidates = [
        *get_base_scim_object_classes(bundle),
        *get_embedded_object_classes_from_scim_schemas(bundle.schemas),
    ]
    return ObjectClassesConfidenceResponse(
        object_classes=[
            ObjectClassWithConfidence(
                name=candidate["name"],
                description=candidate["description"],
                confidence=assignments.get(candidate["name"], ConfidenceLevel.LOW),
            )
            for candidate in candidates
        ]
    )


def _classification_chain(bundle, assignments: dict[str, ConfidenceLevel] | None = None):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_confidence_response(bundle, assignments))
    return chain


def test_build_embedded_object_class_name_preserves_schema_attribute_plurality():
    assert build_embedded_object_class_name("User", "phoneNumbers") == "UserPhoneNumbers"
    assert build_embedded_object_class_name("User", "addresses") == "UserAddresses"
    assert build_embedded_object_class_name("User", "ims") == "UserIms"
    assert build_embedded_object_class_name("Group", "members") == "GroupMembers"
    assert build_embedded_object_class_name("User", "name") == "UserName"


def test_get_embedded_object_classes_from_scim_schema_uses_complex_attributes_only():
    schema = {
        "attributes": [
            {
                "name": "userName",
                "type": "string",
                "description": "Unique identifier for the User.",
            },
            {
                "name": "phoneNumbers",
                "type": "complex",
                "multiValued": True,
                "description": "Phone numbers for the User.",
                "subAttributes": [{"name": "value", "type": "string"}],
            },
        ]
    }

    embedded_classes = get_embedded_object_classes_from_scim_schema("User", schema)

    assert embedded_classes == [
        {
            "name": "UserPhoneNumbers",
            "superclass": None,
            "abstract": False,
            "embedded": True,
            "description": "Phone numbers for the User.",
            "sourceAttribute": "phoneNumbers",
            "sourceClass": "User",
        }
    ]


@pytest.mark.asyncio
async def test_custom_extraction_filters_whitespace_variant_of_baseline_extension():
    extracted_extension = ExtendedObjectClass(
        name="Enterprise User",
        description="Enterprise extension",
        superclass="User",
        embedded=False,
    )

    with patch(
        "src.modules.digester.extractors.scim.object_class.extract_single_chunk",
        new_callable=AsyncMock,
        return_value=([extracted_extension], True),
    ):
        custom_classes, has_relevant_data = await extract_custom_scim_classes(
            schema="Enterprise User",
            job_id=uuid4(),
            scim_base_schemas={"EnterpriseUser": BASELINE_SCHEMAS["EnterpriseUser"]},
        )

    assert custom_classes == []
    assert has_relevant_data is False


@pytest.mark.asyncio
async def test_extract_scim_object_classes_filters_llm_input_only_by_content_type():
    json_with_conndev_filename = {
        "docId": str(uuid4()),
        "chunkId": str(uuid4()),
        "url": "upload://conndev_ScimSchema_NotMarked.json",
        "content": '{"name":"NotMarked"}',
        "@metadata": {
            "filename": "conndev_ScimSchema_NotMarked.json",
            "content_type": "application/json",
        },
    }
    conndev_with_arbitrary_filename = {
        "docId": str(uuid4()),
        "chunkId": str(uuid4()),
        "url": "upload://identity-definition.json",
        "content": '{"name":"Marked"}',
        "@metadata": {
            "filename": "identity-definition.json",
            "content_type": "application/com.evolveum.conndev+json",
        },
    }
    empty_bundle = build_scim_baseline_bundle({})

    with (
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.scim.object_class.build_chunk_extraction_chain",
            return_value=MagicMock(),
        ),
        patch(
            "src.modules.digester.extractors.scim.object_class.run_chunks_concurrently",
            new_callable=AsyncMock,
            return_value=[],
        ) as run_chunks,
        patch(
            "src.modules.digester.extractors.scim.object_class.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=empty_bundle,
        ),
    ):
        await extract_scim_object_classes(
            [json_with_conndev_filename, conndev_with_arbitrary_filename],
            uuid4(),
            uuid4(),
        )

    assert run_chunks.await_args.kwargs["chunk_items"] == [json_with_conndev_filename]


@pytest.mark.asyncio
async def test_extract_scim_object_classes_includes_standard_embedded_classes():
    classification_chain = _classification_chain(BASELINE_BUNDLE)
    with (
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.make_basic_chain",
            return_value=classification_chain,
        ),
        patch(
            "src.modules.digester.extractors.scim.object_class.run_chunks_concurrently", new_callable=AsyncMock
        ) as run_chunks,
        patch(
            "src.modules.digester.extractors.scim.object_class.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
    ):
        run_chunks.return_value = []

        result = await extract_scim_object_classes([], uuid4(), uuid4())

    object_classes = result["result"]["objectClasses"]
    by_name = {item["name"]: item for item in object_classes}

    assert by_name["User"]["embedded"] is False
    assert by_name["EnterpriseUser"]["embedded"] is True
    assert by_name["EnterpriseUser"]["superclass"] is None
    assert by_name["UserName"]["embedded"] is True
    assert by_name["UserName"]["superclass"] is None
    assert by_name["UserPhoneNumbers"]["embedded"] is True
    assert by_name["UserPhoneNumbers"]["superclass"] is None
    assert by_name["GroupMembers"]["embedded"] is True
    assert by_name["GroupMembers"]["superclass"] is None


@pytest.mark.asyncio
async def test_extract_scim_object_classes_propagates_schema_provenance_to_derived_classes():
    reference = ChunkReference(doc_id=str(uuid4()), chunk_id=str(uuid4()))
    bundle = build_scim_baseline_bundle(
        {"User": BASELINE_SCHEMAS["User"]},
        schema_references={"User": reference},
    )
    classification_chain = _classification_chain(bundle)

    with (
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.make_basic_chain",
            return_value=classification_chain,
        ),
        patch(
            "src.modules.digester.extractors.scim.object_class.run_chunks_concurrently",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.extractors.scim.object_class.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=bundle,
        ),
    ):
        result = await extract_scim_object_classes([], uuid4(), uuid4())

    by_name = {item["name"]: item for item in result["result"]["objectClasses"]}
    assert by_name["User"]["relevantDocumentations"] == [reference.to_api_dict()]
    assert by_name["UserName"]["relevantDocumentations"] == [reference.to_api_dict()]


@pytest.mark.asyncio
async def test_extract_scim_object_classes_uses_llm_confidence_and_rest_bucket_sorting():
    candidates = [
        *get_base_scim_object_classes(BASELINE_BUNDLE),
        *get_embedded_object_classes_from_scim_schemas(BASELINE_BUNDLE.schemas),
    ]
    assignments = {candidate["name"]: ConfidenceLevel.MEDIUM for candidate in candidates}
    assignments.update(
        {
            "User": ConfidenceLevel.HIGH,
            "Group": ConfidenceLevel.HIGH,
            "EnterpriseUser": ConfidenceLevel.LOW,
        }
    )
    classification_chain = _classification_chain(BASELINE_BUNDLE, assignments)
    sorting_chain = MagicMock()
    sorting_chain.ainvoke = AsyncMock(
        return_value=ObjectClassesRankedResponse(
            object_classes=[
                RankedObjectClass(
                    name="User",
                    description=BASELINE_SCHEMAS["User"]["description"],
                    relevant=RelevantLevel.TRUE,
                    confidence=ConfidenceLevel.HIGH,
                ),
                RankedObjectClass(
                    name="Group",
                    description=BASELINE_SCHEMAS["Group"]["description"],
                    relevant=RelevantLevel.TRUE,
                    confidence=ConfidenceLevel.HIGH,
                ),
            ]
        )
    )

    with (
        patch("src.modules.digester.extractors.scim.object_class.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.aggregation.object_class_ranking.get_default_llm", return_value=MagicMock()),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.make_basic_chain",
            return_value=classification_chain,
        ),
        patch(
            "src.modules.digester.aggregation.object_class_ranking.build_structured_chain",
            return_value=sorting_chain,
        ) as build_sort_chain,
        patch(
            "src.modules.digester.extractors.scim.object_class.run_chunks_concurrently",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.extractors.scim.object_class.load_session_scim_baseline",
            new_callable=AsyncMock,
            return_value=BASELINE_BUNDLE,
        ),
    ):
        result = await extract_scim_object_classes([], uuid4(), uuid4())

    object_classes = result["result"]["objectClasses"]
    assert [item["name"] for item in object_classes[:2]] == ["User", "Group"]
    assert [item["confidence"] for item in object_classes[:2]] == ["high", "high"]
    assert object_classes[-1]["name"] == "EnterpriseUser"
    assert object_classes[-1]["confidence"] == "low"

    medium_names = [item["name"] for item in object_classes if item["confidence"] == "medium"]
    assert medium_names == sorted(medium_names, key=str.lower)
    assert all(item["relevant"] == "true" for item in object_classes)
    build_sort_chain.assert_called_once()
