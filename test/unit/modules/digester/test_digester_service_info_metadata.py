# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from src.common.enums import ApiType
from src.modules.digester import service
from src.modules.digester.aggregation.merges import merge_api_type, merge_info_metadata
from src.modules.digester.enums import EndpointType
from src.modules.digester.schemas import ApiTypeResponse, BaseAPIEndpoint, InfoMetadata, InfoMetadataExtraction


# ==================== EXTRACT INFO METADATA ====================
@pytest.mark.asyncio
async def test_extract_info_metadata_success(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = uuid4()
    doc_uuid2 = uuid4()

    fake_doc_items = [
        {"uuid": str(doc_uuid1), "content": "API Overview: ExampleAPI v1.0"},
        {"uuid": str(doc_uuid2), "content": "Base URL: https://api.example.com/v1"},
    ]

    info_results = [
        (
            [
                InfoMetadataExtraction(
                    name="ExampleAPI",
                    api_version="v1.0",
                    application_version="1.0.0",
                    base_api_endpoint=[],
                )
            ],
            True,
            doc_uuid1,
        ),
        (
            [
                InfoMetadataExtraction(
                    name="ExampleAPI",
                    api_version="v1.0",
                    application_version="1.0.0",
                    base_api_endpoint=[BaseAPIEndpoint(uri="https://api.example.com/v1", type=EndpointType.CONSTANT)],
                )
            ],
            True,
            doc_uuid2,
        ),
    ]
    api_type_results = [
        ([ApiTypeResponse(api_type=[ApiType.REST, ApiType.SCIM])], True, doc_uuid1),
        ([ApiTypeResponse(api_type=[ApiType.REST, ApiType.SCIM])], True, doc_uuid2),
    ]

    with (
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        # First gather call extracts info metadata, second detects apiType.
        mock_parallel.side_effect = [info_results, api_type_results]

        job_id = uuid4()
        result = await service.extract_info_metadata(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result

        metadata = result["result"]["infoMetadata"]
        assert metadata["name"] == "ExampleAPI"
        assert metadata["apiVersion"] == "v1.0"
        assert len(metadata["baseApiEndpoint"]) == 1
        assert metadata["apiType"] == [ApiType.REST.value, ApiType.SCIM.value]

        assert mock_parallel.await_count == 2


@pytest.mark.asyncio
async def test_extract_info_metadata_empty_docs(mock_llm, mock_digester_update_job_progress):
    """Test extract_info_metadata with no documentation items."""
    with patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock):
        result = await service.extract_info_metadata([], uuid4())

        assert result["result"] == {"infoMetadata": None}
        assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_info_metadata_passes_doc_metadata_to_extractor(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = uuid4()
    doc_uuid2 = uuid4()

    fake_doc_items = [
        {
            "chunkId": str(doc_uuid1),
            "content": "doc 1",
            "summary": "Summary one",
            "@metadata": {"tags": ["rest", "users"]},
        },
        {
            "chunkId": str(doc_uuid2),
            "content": "doc 2",
            "summary": "Summary two",
            "@metadata": {"tags": "openapi"},
        },
    ]

    with (
        patch("src.modules.digester.service._extract_info_metadata", new_callable=AsyncMock) as mock_extract,
        patch("src.modules.digester.service._extract_api_type", new_callable=AsyncMock) as mock_extract_api_type,
        patch("src.modules.digester.service.run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_extract_api_type.return_value = ([], False)
        mock_extract.side_effect = [
            (
                [
                    InfoMetadata(
                        name="ExampleAPI",
                        api_version="1",
                        application_version="1.0.0",
                        api_type=[ApiType.REST],
                        base_api_endpoint=[],
                    )
                ],
                True,
            ),
            (
                [
                    InfoMetadata(
                        name="ExampleAPI",
                        api_version="1",
                        application_version="1.0.0",
                        api_type=[ApiType.REST, ApiType.SCIM],
                        base_api_endpoint=[],
                    )
                ],
                True,
            ),
        ]

        async def run_extractor_for_docs(*, chunk_items, job_id, extractor, logger_scope, set_total=True):
            out = []
            for item in chunk_items:
                result, has_relevant = await extractor(item["content"], job_id, UUID(item["chunkId"]))
                out.append((result, has_relevant, UUID(item["chunkId"])))
            return out

        mock_parallel.side_effect = run_extractor_for_docs

        await service.extract_info_metadata(fake_doc_items, uuid4())

        first_call = mock_extract.await_args_list[0]
        assert first_call.args[3] == {
            "summary": "Summary one",
            "@metadata": {"tags": ["rest", "users"]},
        }

        second_call = mock_extract.await_args_list[1]
        assert second_call.args[3] == {
            "summary": "Summary two",
            "@metadata": {"tags": "openapi"},
        }


def test_merge_info_metadata_preserves_unknown_endpoint_type_when_unknown_is_majority():
    uri = "https://api.example.com/v1"
    info_candidates = [
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.UNKNOWN)]),
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.UNKNOWN)]),
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
    ]

    merged = merge_info_metadata(info_candidates, total_items=3, api_types=[ApiType.REST])
    base_api_endpoints = merged["infoMetadata"]["baseApiEndpoint"]

    assert len(base_api_endpoints) == 1
    assert base_api_endpoints[0]["uri"] == uri.lower()
    assert base_api_endpoints[0]["type"] == ""


def test_merge_info_metadata_uses_unknown_endpoint_type_when_constant_and_dynamic_tie():
    uri = "https://api.example.com/v1"
    info_candidates = [
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.DYNAMIC)]),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.REST])
    base_api_endpoints = merged["infoMetadata"]["baseApiEndpoint"]

    assert len(base_api_endpoints) == 1
    assert base_api_endpoints[0]["uri"] == uri.lower()
    assert base_api_endpoints[0]["type"] == ""


def test_merge_info_metadata_preserves_sql_api_type():
    info_candidates = [
        InfoMetadataExtraction(),
        InfoMetadataExtraction(),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.SQL])

    assert merged["infoMetadata"]["apiType"] == [ApiType.SQL.value]


# ==================== MERGE API TYPE ====================
def test_merge_api_type_keeps_types_above_threshold_sorted():
    candidates = [
        ApiTypeResponse(api_type=[ApiType.SCIM]),
        ApiTypeResponse(api_type=[ApiType.SCIM, ApiType.REST]),
        ApiTypeResponse(api_type=[ApiType.REST]),
    ]

    assert merge_api_type(candidates, total_items=3) == [ApiType.REST, ApiType.SCIM]


def test_merge_api_type_ignores_sparse_noise_below_threshold():
    # 1 SCIM vote out of 100 docs is below the uncertainty threshold and must be dropped.
    candidates = [ApiTypeResponse(api_type=[ApiType.SCIM])]

    assert merge_api_type(candidates, total_items=100) == []


def test_merge_api_type_returns_empty_without_documents():
    assert merge_api_type([ApiTypeResponse(api_type=[ApiType.REST])], total_items=0) == []
