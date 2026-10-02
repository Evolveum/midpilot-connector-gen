# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.documents.selection import DocumentationSelection, SelectionRole
from src.modules.digester.enums import EndpointMethod
from src.modules.digester.errors import EndpointExtractionNotSupportedError
from src.modules.digester.extractors.endpoints import extract_endpoints
from src.modules.digester.extractors.scim.baseline import ScimBaselineBundle
from src.modules.digester.schemas import EndpointInfo
from src.shared.enums import ApiType

CONNDEV = "application/com.evolveum.conndev+json"
_EMPTY = {"result": {"endpoints": []}, "relevantDocumentations": []}


def _doc(content: str, *, content_type: str | None = None) -> dict:
    metadata: dict = {"tags": []}
    if content_type is not None:
        metadata["content_type"] = content_type
    return {
        "docId": str(uuid4()),
        "chunkId": str(uuid4()),
        "url": None,
        "summary": f"summary: {content}",
        "content": content,
        "@metadata": metadata,
    }


def _selection(*, primary=(), fallback=(), scim_baseline=()) -> DocumentationSelection:
    corpus = list({item["chunkId"]: item for item in [*primary, *fallback, *scim_baseline]}.values())
    return DocumentationSelection.from_corpus(
        corpus,
        {
            SelectionRole.PRIMARY: list(primary),
            SelectionRole.FALLBACK: list(fallback),
            SelectionRole.SCIM_BASELINE: list(scim_baseline),
        },
    )


def _endpoints_result(path: str, doc: dict) -> dict:
    return {
        "result": {
            "endpoints": [EndpointInfo(method=EndpointMethod.GET, path=path, description=f"List {path}").model_dump()]
        },
        "relevantDocumentations": [{"doc_id": doc["docId"], "chunk_id": doc["chunkId"]}],
    }


def _persist(return_value: bool = True):
    return patch(
        "src.modules.digester.persistence.update_object_class_field_in_session",
        new_callable=AsyncMock,
        return_value=return_value,
    )


@pytest.mark.asyncio
async def test_extract_endpoints_reads_primary_chunks_and_passes_base_url(mock_digester_update_job_progress):
    primary = _doc("GET /users lists users")
    expected = _endpoints_result("/users", primary)

    with (
        patch(
            "src.modules.digester.extractors.endpoints._extract_rest_endpoints",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_rest,
        _persist() as mock_persist,
    ):
        result = await extract_endpoints(
            _selection(primary=[primary]),
            "User",
            uuid4(),
            uuid4(),
            ApiType.REST,
            "https://custom-api.example.com/v2",
        )

    assert result == expected
    contents, object_class, _, base_api_url, chunk_ids, metadata_map, id_map = mock_rest.await_args.args
    assert (contents, object_class, base_api_url) == (
        [primary["content"]],
        "User",
        "https://custom-api.example.com/v2",
    )
    assert chunk_ids == [primary["chunkId"]]
    assert set(metadata_map) == {primary["chunkId"]}
    assert id_map == {primary["chunkId"]: primary["docId"]}
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_endpoints_without_primary_chunks_returns_empty_without_persisting(
    mock_digester_update_job_progress,
):
    with (
        patch("src.modules.digester.extractors.endpoints._extract_rest_endpoints", new_callable=AsyncMock) as rest,
        _persist() as mock_persist,
    ):
        result = await extract_endpoints(_selection(), "User", uuid4(), uuid4(), ApiType.REST)

    assert result == _EMPTY
    rest.assert_not_awaited()
    mock_persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_endpoints_retries_over_stored_fallback_when_primary_is_empty(
    mock_digester_update_job_progress,
):
    primary = _doc("Endpoint overview without paths")
    fallback = _doc("GET /groups lists groups")
    fallback_result = _endpoints_result("/groups", fallback)

    with (
        patch(
            "src.modules.digester.extractors.endpoints._extract_rest_endpoints",
            new_callable=AsyncMock,
            side_effect=[_EMPTY, fallback_result],
        ) as mock_rest,
        _persist() as mock_persist,
    ):
        result = await extract_endpoints(
            _selection(primary=[primary], fallback=[fallback]), "Group", uuid4(), uuid4(), ApiType.REST
        )

    assert result == fallback_result
    assert [call.args[0] for call in mock_rest.await_args_list] == [[primary["content"]], [fallback["content"]]]
    mock_digester_update_job_progress.assert_awaited()
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_endpoints_keeps_empty_primary_when_no_fallback_was_stored(mock_digester_update_job_progress):
    primary = _doc("Endpoint overview without paths")

    with (
        patch(
            "src.modules.digester.extractors.endpoints._extract_rest_endpoints",
            new_callable=AsyncMock,
            return_value=_EMPTY,
        ) as mock_rest,
        _persist() as mock_persist,
    ):
        result = await extract_endpoints(_selection(primary=[primary]), "Group", uuid4(), uuid4(), ApiType.REST)

    assert result == _EMPTY
    mock_rest.assert_awaited_once()
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_endpoints_does_not_retry_when_primary_found_endpoints(mock_digester_update_job_progress):
    primary = _doc("GET /users")
    with (
        patch(
            "src.modules.digester.extractors.endpoints._extract_rest_endpoints",
            new_callable=AsyncMock,
            return_value=_endpoints_result("/users", primary),
        ) as mock_rest,
        _persist(),
    ):
        await extract_endpoints(
            _selection(primary=[primary], fallback=[_doc("GET /groups")]), "User", uuid4(), uuid4(), ApiType.REST
        )

    mock_rest.assert_awaited_once()


@pytest.mark.asyncio
async def test_scim_extract_endpoints_reads_scraped_documentation_when_conndev_has_no_endpoint(
    mock_digester_update_job_progress,
):
    conndev = _doc('{"schemaContent": "..."}', content_type=CONNDEV)
    scraped = _doc("GET /api/actions", content_type="text/html")
    documented = _endpoints_result("/api/actions", scraped)
    job_id = uuid4()

    with (
        patch(
            "src.modules.digester.extractors.endpoints.pregenerate_scim_endpoints",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_pregenerate,
        patch(
            "src.modules.digester.extractors.endpoints._extract_rest_endpoints",
            new_callable=AsyncMock,
            return_value=documented,
        ) as mock_rest,
        _persist(),
    ):
        result = await extract_endpoints(
            _selection(primary=[conndev, scraped], scim_baseline=[conndev]),
            "Action",
            uuid4(),
            job_id,
            ApiType.SCIM,
            "https://example.test",
        )

    assert result == documented
    pregenerate_kwargs = mock_pregenerate.await_args.kwargs
    assert isinstance(pregenerate_kwargs["baseline_bundle"], ScimBaselineBundle)
    assert pregenerate_kwargs["object_class"] == "Action"
    assert pregenerate_kwargs["job_id"] == job_id
    # Conndev contracts are pregeneration input, never LLM documentation.
    assert mock_rest.await_args.args[0] == [scraped["content"]]


@pytest.mark.asyncio
async def test_scim_extract_endpoints_does_not_fall_back_for_terminal_non_resource(
    mock_digester_update_job_progress,
):
    terminal_result = {"result": {"endpoints": []}, "relevantDocumentations": []}

    with (
        patch(
            "src.modules.digester.extractors.endpoints.pregenerate_scim_endpoints",
            new_callable=AsyncMock,
            return_value=terminal_result,
        ),
        patch("src.modules.digester.extractors.endpoints._extract_rest_endpoints", new_callable=AsyncMock) as rest,
        _persist(),
    ):
        result = await extract_endpoints(
            _selection(fallback=[_doc("GET /names")]),
            "UserName",
            uuid4(),
            uuid4(),
            ApiType.SCIM,
            object_class_flags={"embedded": True, "abstract": False},
        )

    assert result == terminal_result
    rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_scim_documentation_fallback_returns_empty_when_only_conndev_documents_exist(
    mock_digester_update_job_progress,
):
    conndev = _doc('{"schemaContent": "..."}', content_type=CONNDEV)

    with (
        patch(
            "src.modules.digester.extractors.endpoints.pregenerate_scim_endpoints",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.modules.digester.extractors.endpoints._extract_rest_endpoints", new_callable=AsyncMock) as rest,
        _persist(),
    ):
        result = await extract_endpoints(
            _selection(primary=[conndev], scim_baseline=[conndev]), "Action", uuid4(), uuid4(), ApiType.SCIM
        )

    assert result == _EMPTY
    rest.assert_not_awaited()


@pytest.mark.asyncio
async def test_sql_endpoint_extraction_is_rejected_by_the_worker(mock_digester_update_job_progress):
    with pytest.raises(EndpointExtractionNotSupportedError):
        await extract_endpoints(_selection(), "users", uuid4(), uuid4(), ApiType.SQL)
