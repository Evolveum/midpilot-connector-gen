# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.errors import AppError
from src.modules.digester.errors import RelevantChunksNotFoundError
from src.modules.digester.selection import DocumentationSelector


@pytest.mark.asyncio
async def test_attribute_plan_uses_scim_object_class_relevance_when_filter_has_no_chunks():
    session_id = uuid4()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    doc_items = [{"docId": doc_id, "chunkId": chunk_id, "content": "SCIM User mapping docs"}]

    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={
            "objectClasses": [
                {
                    "name": "UserPhoneNumbers",
                    "superclass": "User",
                    "embedded": True,
                }
            ]
        }
    )
    relevant_repo = MagicMock()
    relevant_repo.get_relevant_chunks_grouped_by_entity = AsyncMock(
        return_value={"user": [{"docId": doc_id, "chunkId": chunk_id}]}
    )

    with (
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=["scim"],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new_callable=AsyncMock,
            return_value=doc_items,
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.RelevantChunkRepository",
            return_value=relevant_repo,
        ),
    ):
        plan = await DocumentationSelector(MagicMock()).build_attribute_plan(
            repo=repo,
            session_id=session_id,
            object_class="UserPhoneNumbers",
        )

    assert plan.doc_items == doc_items
    assert plan.relevant_chunks == [{"doc_id": doc_id, "chunk_id": chunk_id}]
    relevant_repo.get_relevant_chunks_grouped_by_entity.assert_awaited_once_with(
        session_id=session_id,
        result_key="objectClassesOutput",
    )


@pytest.mark.asyncio
async def test_endpoint_plan_uses_default_criteria_when_endpoint_filter_has_no_chunks():
    session_id = uuid4()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    doc_items = [{"docId": doc_id, "chunkId": chunk_id, "content": "GET /users endpoint docs"}]

    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={
            "objectClasses": [
                {
                    "name": "User",
                    "superclass": "",
                }
            ]
        }
    )

    with (
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_base_api_url",
            new_callable=AsyncMock,
            return_value="https://api.example.com",
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new_callable=AsyncMock,
            side_effect=[[], [{"docId": doc_id, "chunkId": chunk_id}]],
        ) as mock_filter,
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new_callable=AsyncMock,
            return_value=doc_items,
        ),
    ):
        plan = await DocumentationSelector(MagicMock()).build_endpoint_plan(
            repo=repo,
            session_id=session_id,
            object_class="User",
        )

    assert plan.base_api_url == "https://api.example.com"
    assert plan.doc_items == doc_items
    assert plan.relevant_chunks == [{"doc_id": doc_id, "chunk_id": chunk_id}]
    assert plan.object_class_flags == {}
    assert mock_filter.await_count == 2
    assert mock_filter.await_args_list[0].args[1] == session_id
    assert mock_filter.await_args_list[1].args[1] == session_id


@pytest.mark.asyncio
async def test_scim_endpoint_plan_carries_structural_flags_into_job_input():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={
            "objectClasses": [
                {
                    "name": "UserPhoneNumbers",
                    "embedded": "true",
                    "abstract": False,
                }
            ]
        }
    )

    with (
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=["scim"],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_base_api_url",
            new_callable=AsyncMock,
            return_value="",
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.RelevantChunkRepository",
            return_value=MagicMock(
                get_relevant_chunks_grouped_by_entity=AsyncMock(return_value={}),
            ),
        ),
    ):
        plan = await DocumentationSelector(MagicMock()).build_endpoint_plan(
            repo=repo,
            session_id=session_id,
            object_class="UserPhoneNumbers",
        )

    assert plan.object_class_flags == {"embedded": True, "abstract": False}


@pytest.mark.asyncio
async def test_attribute_plan_uses_sql_schema_chunks_without_rest_filtering():
    session_id = uuid4()
    doc_id = str(uuid4())
    chunk_id = str(uuid4())
    doc_items = [
        {
            "docId": doc_id,
            "chunkId": chunk_id,
            "content": "CREATE TABLE users (id UUID PRIMARY KEY, username TEXT NOT NULL);",
            "@metadata": {
                "category": "reference_other",
                "num_endpoints": 0,
                "content_type": "text/sql",
            },
        }
    ]

    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value={"objectClasses": [{"name": "User", "superclass": ""}]})
    relevant_repo = MagicMock()
    relevant_repo.get_relevant_chunks_grouped_by_entity = AsyncMock(return_value={})

    with (
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=["sql"],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_filter,
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_documentation",
            new_callable=AsyncMock,
            return_value=doc_items,
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.RelevantChunkRepository",
            return_value=relevant_repo,
        ),
    ):
        plan = await DocumentationSelector(MagicMock()).build_attribute_plan(
            repo=repo,
            session_id=session_id,
            object_class="User",
        )

    assert plan.doc_items == doc_items
    assert plan.relevant_chunks == [{"doc_id": doc_id, "chunk_id": chunk_id}]
    mock_filter.assert_not_awaited()
    relevant_repo.get_relevant_chunks_grouped_by_entity.assert_awaited_once_with(
        session_id=session_id,
        result_key="objectClassesOutput",
    )


@pytest.mark.asyncio
async def test_attribute_plan_rejects_rest_without_relevant_chunks():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={
            "objectClasses": [
                {
                    "name": "User",
                    "superclass": "",
                }
            ]
        }
    )

    with (
        patch(
            "src.modules.digester.selection.documentation_selector.get_session_api_types",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.modules.digester.selection.documentation_selector.filter_documentation_items",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        with pytest.raises(RelevantChunksNotFoundError):
            await DocumentationSelector(MagicMock()).build_attribute_plan(
                repo=repo,
                session_id=session_id,
                object_class="User",
            )


@pytest.mark.parametrize(
    "output,status,code",
    [
        (None, 404, "object_classes_not_found"),
        ({}, 404, "object_classes_not_found"),
        ("invalid", 404, "object_classes_not_found"),
        ({"foo": 1}, 404, "object_class_not_found"),
        ({"objectClasses": []}, 404, "object_class_not_found"),
        ({"objectClasses": None}, 422, "invalid_object_classes_output"),
        ({"objectClasses": {}}, 422, "invalid_object_classes_output"),
    ],
)
@pytest.mark.asyncio
async def test_selector_preserves_shared_class_lookup_errors(output, status, code):
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value=output)
    selector = DocumentationSelector(MagicMock())
    with pytest.raises(AppError) as error:
        await selector._get_target_object_class(repo, uuid4(), "Group")
    assert error.value.status_code == status
    assert error.value.code == code
