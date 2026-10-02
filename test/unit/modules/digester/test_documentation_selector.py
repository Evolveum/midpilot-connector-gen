# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.errors import AppError
from src.documents.selection import SelectionRole
from src.modules.digester.errors import RelevantChunksNotFoundError
from src.modules.digester.selection import DocumentationSelector
from src.shared.enums import ApiType

CONNDEV = "application/com.evolveum.conndev+json"


def _item(
    content: str,
    *,
    category: str = "spec_yaml",
    tags: list[str] | None = None,
    num_endpoints: int = 0,
    content_type: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"category": category, "tags": tags or [], "num_endpoints": num_endpoints}
    if content_type is not None:
        metadata["content_type"] = content_type
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": f"https://docs.example.com/{content.split()[0].lower()}",
        "summary": f"summary of {content}",
        "content": content,
        "@metadata": metadata,
    }


def _repo(object_classes: list[dict[str, Any]]) -> MagicMock:
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value={"objectClasses": object_classes})
    return repo


def _relevant_repo(by_entity: dict[str, list[dict[str, str]]]) -> MagicMock:
    return MagicMock(get_relevant_chunks_grouped_by_entity=AsyncMock(return_value=by_entity))


def _selector(corpus: list[dict[str, Any]], *, by_entity=None, base_url: str = "") -> tuple[DocumentationSelector, Any]:
    load_corpus = AsyncMock(return_value=corpus)
    relevant_repo = _relevant_repo(by_entity or {})
    selector = DocumentationSelector(
        MagicMock(),
        load_corpus=load_corpus,
        get_base_url=AsyncMock(return_value=base_url),
        relevant_repo_factory=lambda _db: relevant_repo,
    )
    return selector, load_corpus


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["chunkId"] for item in items]


@pytest.mark.asyncio
async def test_attribute_plan_derives_primary_and_fallback_from_one_corpus_load():
    user_spec = _item("User schema", tags=["user"])
    group_spec = _item("Group schema", tags=["group"])
    overview = _item("Overview prose", category="overview", tags=["user"])
    selector, load_corpus = _selector([user_spec, group_spec, overview])

    plan = await selector.build_attribute_plan(
        repo=_repo([{"name": "User"}]), session_id=uuid4(), object_class="user", protocol=ApiType.REST
    )

    load_corpus.assert_awaited_once()
    selection = plan.selection
    assert _ids(selection.documentation_items(SelectionRole.PRIMARY)) == _ids([user_spec])
    # The fallback is DEFAULT_CRITERIA over the same snapshot minus every primary chunk.
    assert _ids(selection.documentation_items(SelectionRole.FALLBACK)) == _ids([group_spec])
    assert selection.scim_baseline == ()
    assert _ids([chunk.to_documentation_item() for chunk in selection.chunks]) == _ids([user_spec, group_spec])
    assert plan.relevant_chunk_count == 1


@pytest.mark.asyncio
async def test_attribute_plan_stores_no_fallback_when_default_criteria_matches_only_primary_chunks():
    user_spec = _item("User schema", tags=["user"])
    selector, _ = _selector([user_spec])

    plan = await selector.build_attribute_plan(
        repo=_repo([{"name": "User"}]), session_id=uuid4(), object_class="user", protocol=ApiType.REST
    )

    assert plan.selection.fallback == ()


@pytest.mark.asyncio
async def test_scim_attribute_plan_uses_object_class_relevance_and_stores_conndev_baseline():
    prose = _item("User mapping guide", category="overview")
    conndev = _item("{}", category="spec_json", tags=["scim", "schema", "conndev"], content_type=CONNDEV)
    stale_ref = {"docId": str(uuid4()), "chunkId": str(uuid4())}
    selector, _ = _selector(
        [prose, conndev],
        by_entity={
            "userphonenumbers": [{"docId": prose["docId"], "chunkId": prose["chunkId"]}],
            "user": [stale_ref],
        },
    )

    plan = await selector.build_attribute_plan(
        repo=_repo([{"name": "UserPhoneNumbers", "superclass": "User", "embedded": True}]),
        session_id=uuid4(),
        object_class="UserPhoneNumbers",
        protocol=ApiType.SCIM,
    )

    selection = plan.selection
    # The superclass relevance points at a chunk the session no longer holds; it is dropped.
    assert selection.relevant_chunks(SelectionRole.PRIMARY) == [
        {"doc_id": prose["docId"], "chunk_id": prose["chunkId"]}
    ]
    assert _ids(selection.documentation_items(SelectionRole.SCIM_BASELINE)) == _ids([conndev])
    assert _ids(selection.documentation_items(SelectionRole.FALLBACK)) == _ids([conndev])


@pytest.mark.asyncio
async def test_sql_attribute_plan_keeps_the_whole_corpus_for_schema_joins():
    table = _item(
        "CREATE TABLE users (id UUID PRIMARY KEY, username TEXT NOT NULL);",
        category="reference_other",
        content_type="text/sql",
    )
    prose = _item("Unrelated overview", category="overview")
    selector, _ = _selector([table, prose])

    plan = await selector.build_attribute_plan(
        repo=_repo([{"name": "User", "superclass": ""}]),
        session_id=uuid4(),
        object_class="User",
        protocol=ApiType.SQL,
    )

    selection = plan.selection
    assert _ids(selection.documentation_items(SelectionRole.SQL_SCHEMA)) == _ids([table, prose])
    assert selection.primary == () and selection.fallback == ()
    assert plan.relevant_chunk_count == 1


@pytest.mark.asyncio
async def test_rest_attribute_plan_without_relevant_chunks_is_rejected():
    selector, _ = _selector([_item("Group schema", tags=["group"])])

    with pytest.raises(RelevantChunksNotFoundError):
        await selector.build_attribute_plan(
            repo=_repo([{"name": "User"}]), session_id=uuid4(), object_class="user", protocol=ApiType.REST
        )


@pytest.mark.asyncio
async def test_endpoint_plan_uses_default_criteria_when_endpoint_filter_has_no_chunks():
    spec = _item("GET users", tags=["api"])
    selector, load_corpus = _selector([spec], base_url="https://api.example.com")

    plan = await selector.build_endpoint_plan(
        repo=_repo([{"name": "User", "superclass": ""}]),
        session_id=uuid4(),
        object_class="User",
        protocol=ApiType.REST,
    )

    load_corpus.assert_awaited_once()
    assert plan.base_api_url == "https://api.example.com"
    assert _ids(plan.selection.documentation_items(SelectionRole.PRIMARY)) == _ids([spec])
    # The primary already is the DEFAULT_CRITERIA set, so a retry would repeat it.
    assert plan.selection.fallback == ()
    assert plan.object_class_flags == {}


@pytest.mark.asyncio
async def test_scim_endpoint_plan_carries_flags_and_keeps_conndev_out_of_the_fallback():
    endpoint_doc = _item("GET userphonenumbers", tags=["userphonenumbers", "endpoint"], num_endpoints=1)
    other_spec = _item("Group endpoints", tags=["group"])
    conndev = _item("{}", category="spec_json", tags=["scim", "schema", "conndev"], content_type=CONNDEV)
    selector, _ = _selector([endpoint_doc, other_spec, conndev])

    plan = await selector.build_endpoint_plan(
        repo=_repo([{"name": "UserPhoneNumbers", "embedded": "true", "abstract": False}]),
        session_id=uuid4(),
        object_class="UserPhoneNumbers",
        protocol=ApiType.SCIM,
    )

    assert plan.object_class_flags == {"embedded": True, "abstract": False}
    assert _ids(plan.selection.documentation_items(SelectionRole.PRIMARY)) == _ids([endpoint_doc])
    assert _ids(plan.selection.documentation_items(SelectionRole.FALLBACK)) == _ids([other_spec])
    assert _ids(plan.selection.documentation_items(SelectionRole.SCIM_BASELINE)) == _ids([conndev])


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
async def test_selector_preserves_shared_class_lookup_errors_before_loading_documentation(output, status, code):
    repo = MagicMock()
    repo.get_session_data = AsyncMock(return_value=output)
    selector, load_corpus = _selector([])

    with pytest.raises(AppError) as error:
        await selector.build_attribute_plan(repo=repo, session_id=uuid4(), object_class="Group", protocol=ApiType.REST)

    assert error.value.status_code == status
    assert error.value.code == code
    load_corpus.assert_not_awaited()
