# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from src.modules.digester.aggregation.merges import merge_api_type, merge_info_metadata
from src.modules.digester.enums import EndpointType
from src.modules.digester.extractors.apitype.scim_cloud import ScimCloudMatch
from src.modules.digester.extractors.info import extract_info_metadata
from src.modules.digester.schemas import (
    ApiTypeResponse,
    ApiTypeSignalResult,
    BaseAPIEndpoint,
    InfoMetadata,
    InfoMetadataExtraction,
    RestAvailabilityInfo,
    RestSignalResult,
    ScimAvailabilityInfo,
)
from src.shared.enums import ApiType, DetectionSource, ProtocolAvailability


@pytest.fixture(autouse=True)
def _stub_rest_signals():
    """Stub the documentation-free REST signals to non-supporting by default.

    Keeps the extract-info tests offline (the web-search signal would otherwise hit the
    network) and focused on the SCIM/merge behavior they assert. Tests that exercise REST
    signal detection re-patch these targets explicitly.
    """
    with (
        patch(
            "src.modules.digester.extractors.info.lookup_rest_knowledge",
            new_callable=AsyncMock,
            return_value=RestSignalResult(supports_rest=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_rest_web_search",
            new_callable=AsyncMock,
            return_value=RestSignalResult(supports_rest=False),
        ),
    ):
        yield


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
                    base_api_endpoint=[
                        BaseAPIEndpoint(
                            uri="https://api.example.com/v1",
                            type=EndpointType.CONSTANT,
                            api_type=ApiType.REST,
                        )
                    ],
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
        patch(
            "src.modules.digester.extractors.info.run_doc_extractors_concurrently", new_callable=AsyncMock
        ) as mock_parallel,
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(matched=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
    ):
        # First gather call extracts info metadata, second detects apiType.
        mock_parallel.side_effect = [info_results, api_type_results]

        job_id = uuid4()
        result = await extract_info_metadata(fake_doc_items, "ExampleAPI", job_id)

        assert "result" in result
        assert "relevantDocumentations" in result

        metadata = result["result"]["infoMetadata"]
        assert metadata["name"] == "ExampleAPI"
        assert metadata["apiVersion"] == "v1.0"
        assert len(metadata["restAvailability"]["baseApiEndpoint"]) == 1
        assert metadata["apiType"] == [ApiType.REST.value, ApiType.SCIM.value]

        assert mock_parallel.await_count == 2


_SCIM_CLOUD_MATCH = ScimCloudMatch(matched=True, application_name="Acme", project_name="Acme", scim_versions=["2.0"])
_SCIM_CLOUD_MISS = ScimCloudMatch(matched=False)
_SCIM_SIGNAL = ApiTypeSignalResult(supports_scim=True, api_type=[ApiType.SCIM])
_NO_SCIM_SIGNAL = ApiTypeSignalResult(supports_scim=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scim_cloud", "knowledge_signal", "web_search_signal"),
    [
        (_SCIM_CLOUD_MATCH, _NO_SCIM_SIGNAL, _NO_SCIM_SIGNAL),
        (_SCIM_CLOUD_MISS, _SCIM_SIGNAL, _NO_SCIM_SIGNAL),
        (_SCIM_CLOUD_MISS, _NO_SCIM_SIGNAL, _SCIM_SIGNAL),
    ],
    ids=["scim-cloud", "llm-knowledge", "web-search"],
)
async def test_any_documentation_free_signal_unions_scim_into_api_type(
    scim_cloud: ScimCloudMatch,
    knowledge_signal: ApiTypeSignalResult,
    web_search_signal: ApiTypeSignalResult,
    mock_llm,
    mock_digester_update_job_progress,
):
    """Each documentation-free SCIM signal unions SCIM on its own, beside the REST doc finding."""
    doc_uuid = uuid4()
    fake_doc_items = [{"uuid": str(doc_uuid), "content": "Acme REST API"}]

    info_results = [([InfoMetadataExtraction(name="Acme")], True, doc_uuid)]
    api_type_results = [([ApiTypeResponse(api_type=[ApiType.REST])], True, doc_uuid)]

    with (
        patch(
            "src.modules.digester.extractors.info.run_doc_extractors_concurrently", new_callable=AsyncMock
        ) as mock_parallel,
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=scim_cloud,
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=knowledge_signal,
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=web_search_signal,
        ),
    ):
        mock_parallel.side_effect = [info_results, api_type_results]

        result = await extract_info_metadata(fake_doc_items, "Acme", uuid4())

    assert result["result"]["infoMetadata"]["apiType"] == [ApiType.REST.value, ApiType.SCIM.value]


@pytest.mark.asyncio
async def test_extract_info_metadata_rest_signal_adds_rest_and_exposes_availability(
    mock_llm, mock_digester_update_job_progress
):
    """A REST signal unions REST into apiType and exposes the aggregated restAvailability advisory."""
    doc_uuid = uuid4()
    fake_doc_items = [{"uuid": str(doc_uuid), "content": "Acme"}]

    # Docs detect only SCIM; the documentation-free REST signals establish REST.
    info_results = [([InfoMetadataExtraction(name="Acme")], True, doc_uuid)]
    api_type_results = [([ApiTypeResponse(api_type=[ApiType.SCIM])], True, doc_uuid)]

    with (
        patch(
            "src.modules.digester.extractors.info.run_doc_extractors_concurrently", new_callable=AsyncMock
        ) as mock_parallel,
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(matched=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=True, api_type=[ApiType.SCIM]),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_rest_web_search",
            new_callable=AsyncMock,
            return_value=RestSignalResult(
                supports_rest=True, availability=ProtocolAvailability.PAID, required_plan="Enterprise"
            ),
        ),
    ):
        mock_parallel.side_effect = [info_results, api_type_results]
        result = await extract_info_metadata(fake_doc_items, "Acme", uuid4())

    metadata = result["result"]["infoMetadata"]
    assert metadata["apiType"] == [ApiType.REST.value, ApiType.SCIM.value]
    rest = metadata["restAvailability"]
    assert rest["status"] == ProtocolAvailability.PAID.value
    assert rest["requiredPlan"] == "Enterprise"
    assert rest["sources"] == ["web_search"]
    assert rest["confidence"] == 1.0


@pytest.mark.asyncio
async def test_extract_info_metadata_exposes_scim_availability(mock_llm, mock_digester_update_job_progress):
    """When SCIM is detected, the response carries the aggregated scimAvailability advisory."""
    doc_uuid = uuid4()
    fake_doc_items = [{"uuid": str(doc_uuid), "content": "Acme"}]

    info_results = [([InfoMetadataExtraction(name="Acme")], True, doc_uuid)]
    api_type_results = [([ApiTypeResponse(api_type=[ApiType.REST])], True, doc_uuid)]

    with (
        patch(
            "src.modules.digester.extractors.info.run_doc_extractors_concurrently", new_callable=AsyncMock
        ) as mock_parallel,
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(
                matched=True, application_name="Acme", project_name="Acme", scim_versions=["2.0"]
            ),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(
                supports_scim=True,
                api_type=[ApiType.SCIM],
                scim_availability=ProtocolAvailability.PAID,
                required_plan="Enterprise",
            ),
        ),
    ):
        mock_parallel.side_effect = [info_results, api_type_results]
        result = await extract_info_metadata(fake_doc_items, "Acme", uuid4())

    metadata = result["result"]["infoMetadata"]
    assert ApiType.SCIM.value in metadata["apiType"]
    availability = metadata["scimAvailability"]
    assert availability["status"] == ProtocolAvailability.PAID.value
    assert availability["requiredPlan"] == "Enterprise"
    assert availability["sources"] == ["scim_cloud", "web_search"]
    assert availability["confidence"] == 1.0


@pytest.mark.asyncio
async def test_extract_info_metadata_empty_docs(mock_llm, mock_digester_update_job_progress):
    """Test extract_info_metadata with no documentation items."""
    with (
        patch("src.modules.digester.extractors.info.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(matched=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
    ):
        result = await extract_info_metadata([], "", uuid4())

        assert result["result"] == {"infoMetadata": None}
        assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_info_metadata_no_docs_keeps_signal_scim(mock_llm, mock_digester_update_job_progress):
    """With no documentation but a documentation-free signal confirming SCIM, the SCIM
    detection and its availability advisory must survive instead of being discarded."""
    with (
        patch("src.modules.digester.extractors.info.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(matched=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(
                supports_scim=True,
                api_type=[ApiType.SCIM],
                scim_availability=ProtocolAvailability.PAID,
                required_plan="Enterprise",
            ),
        ),
    ):
        result = await extract_info_metadata([], application_name="Acme", job_id=uuid4())

    metadata = result["result"]["infoMetadata"]
    assert metadata is not None
    assert metadata["apiType"] == [ApiType.SCIM.value]
    assert metadata["scimAvailability"]["status"] == ProtocolAvailability.PAID.value
    assert metadata["scimAvailability"]["requiredPlan"] == "Enterprise"
    assert metadata["scimAvailability"]["sources"] == ["web_search"]


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
        patch(
            "src.modules.digester.extractors.info.extract_info_metadata_chunk", new_callable=AsyncMock
        ) as mock_extract,
        patch(
            "src.modules.digester.extractors.info._extract_api_type", new_callable=AsyncMock
        ) as mock_extract_api_type,
        patch(
            "src.modules.digester.extractors.info.run_doc_extractors_concurrently", new_callable=AsyncMock
        ) as mock_parallel,
        patch(
            "src.modules.digester.extractors.info.lookup_scim_support",
            new_callable=AsyncMock,
            return_value=ScimCloudMatch(matched=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_knowledge",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
        patch(
            "src.modules.digester.extractors.info.lookup_api_type_web_search",
            new_callable=AsyncMock,
            return_value=ApiTypeSignalResult(supports_scim=False),
        ),
    ):
        mock_extract_api_type.return_value = ([], False)
        # _extract_info_metadata yields per-chunk InfoMetadataExtraction (apiType is detected
        # separately and is not part of this model).
        mock_extract.side_effect = [
            (
                [InfoMetadataExtraction(name="ExampleAPI", api_version="1", application_version="1.0.0")],
                True,
            ),
            (
                [InfoMetadataExtraction(name="ExampleAPI", api_version="1", application_version="1.0.0")],
                True,
            ),
        ]

        async def run_extractor_for_docs(*, chunk_items, job_id, extractor, set_total=True):
            out = []
            for item in chunk_items:
                result, has_relevant = await extractor(item["content"], job_id, UUID(item["chunkId"]))
                out.append((result, has_relevant, UUID(item["chunkId"])))
            return out

        mock_parallel.side_effect = run_extractor_for_docs

        await extract_info_metadata(fake_doc_items, "ExampleAPI", uuid4())

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
    base_api_endpoints = merged["infoMetadata"]["restAvailability"]["baseApiEndpoint"]

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
    base_api_endpoints = merged["infoMetadata"]["restAvailability"]["baseApiEndpoint"]

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


def test_merge_info_metadata_buckets_endpoints_by_protocol():
    # Each base endpoint is routed to its protocol-specific block by its api_type tag.
    rest_uri = "https://api.example.com/v2"
    scim_uri = "https://api.example.com/scim/v2"
    endpoints = [
        BaseAPIEndpoint(uri=rest_uri, type=EndpointType.CONSTANT, api_type=ApiType.REST),
        BaseAPIEndpoint(uri=scim_uri, type=EndpointType.CONSTANT, api_type=ApiType.SCIM),
    ]
    info_candidates = [
        InfoMetadataExtraction(base_api_endpoint=endpoints),
        InfoMetadataExtraction(base_api_endpoint=endpoints),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.REST, ApiType.SCIM])
    info = merged["infoMetadata"]

    rest_block = info["restAvailability"]["baseApiEndpoint"]
    scim_block = info["scimAvailability"]["baseApiEndpoint"]
    assert [e["uri"] for e in rest_block] == [rest_uri.lower()]
    assert [e["uri"] for e in scim_block] == [scim_uri.lower()]
    # The block already implies the protocol, so the per-endpoint apiType is not serialized.
    assert "apiType" not in rest_block[0]
    assert "apiType" not in scim_block[0]


def test_base_api_endpoint_leaves_missing_protocol_unclassified():
    endpoint = BaseAPIEndpoint(uri="https://api.example.com/scim/v2")

    assert endpoint.api_type is None


def test_merge_info_metadata_routes_unclassified_endpoint_by_scim_session_api_type():
    uri = "https://api.example.com/scim/v2"
    info_candidates = [
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.SCIM])
    info = merged["infoMetadata"]

    assert info["restAvailability"]["baseApiEndpoint"] == []
    assert [endpoint["uri"] for endpoint in info["scimAvailability"]["baseApiEndpoint"]] == [uri.lower()]


def test_merge_info_metadata_drops_endpoint_protocol_absent_from_session_api_type():
    uri = "https://api.example.com/scim/v2"
    info_candidates = [
        InfoMetadataExtraction(
            base_api_endpoint=[
                BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT, api_type=ApiType.SCIM),
            ]
        ),
        InfoMetadataExtraction(
            base_api_endpoint=[
                BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT, api_type=ApiType.SCIM),
            ]
        ),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.REST])
    info = merged["infoMetadata"]

    assert info["restAvailability"]["baseApiEndpoint"] == []
    assert info["scimAvailability"]["baseApiEndpoint"] == []


def test_merge_info_metadata_defaults_unclassified_endpoint_to_rest_without_session_api_type():
    uri = "https://api.example.com/v1"
    info_candidates = [
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
        InfoMetadataExtraction(base_api_endpoint=[BaseAPIEndpoint(uri=uri, type=EndpointType.CONSTANT)]),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[])
    info = merged["infoMetadata"]

    assert [endpoint["uri"] for endpoint in info["restAvailability"]["baseApiEndpoint"]] == [uri.lower()]
    assert info["scimAvailability"]["baseApiEndpoint"] == []


def test_merge_info_metadata_places_database_name_in_sql_block():
    info_candidates = [
        InfoMetadataExtraction(database_name="hr"),
        InfoMetadataExtraction(database_name="hr"),
    ]

    merged = merge_info_metadata(info_candidates, total_items=2, api_types=[ApiType.SQL])

    assert merged["infoMetadata"]["sqlAvailability"]["databaseName"] == "hr"


# ==================== EXTRACTION EMPTINESS ====================
def test_extraction_is_empty_for_blank_chunk():
    assert InfoMetadataExtraction().is_empty()


@pytest.mark.parametrize(
    "extraction",
    [
        # A chunk documenting just the HTTP base URL must survive extraction (regression: the
        # flat extraction shape was wrongly checked against the grouped final blocks and dropped).
        InfoMetadataExtraction(
            base_api_endpoint=[BaseAPIEndpoint(uri="https://api.example.com/v1/", type=EndpointType.CONSTANT)]
        ),
        InfoMetadataExtraction(database_name="hr"),
        InfoMetadataExtraction(name="Acme"),
    ],
    ids=["base-url-only", "database-name-only", "name-only"],
)
def test_extraction_with_any_single_populated_field_is_not_empty(extraction: InfoMetadataExtraction):
    assert not extraction.is_empty()


# ==================== SCIM AVAILABILITY ====================
def test_info_metadata_serializes_scim_availability_for_scim():
    metadata = InfoMetadata(
        api_type=[ApiType.SCIM],
        scim_availability=ScimAvailabilityInfo(
            status=ProtocolAvailability.PAID, required_plan="Enterprise", sources=[DetectionSource.WEB_SEARCH]
        ),
    )
    dumped = metadata.model_dump(by_alias=True)

    assert dumped["scimAvailability"]["status"] == ProtocolAvailability.PAID.value
    assert dumped["scimAvailability"]["requiredPlan"] == "Enterprise"
    assert dumped["scimAvailability"]["sources"] == ["web_search"]
    assert dumped["scimAvailability"]["confidence"] == 1.0


def test_info_metadata_keeps_all_availability_blocks_when_not_scim():
    # Blocks are never dropped: all three availability blocks are always serialized, empty
    # when the corresponding protocol is not detected.
    metadata = InfoMetadata(api_type=[ApiType.REST])
    dumped = metadata.model_dump(by_alias=True)

    # REST carries the same availability advisory shape as SCIM (default/unknown when the
    # advisory was not populated).
    assert dumped["restAvailability"]["baseApiEndpoint"] == []
    assert dumped["restAvailability"]["status"] == ProtocolAvailability.UNKNOWN.value
    assert dumped["restAvailability"]["requiredPlan"] == ""
    assert dumped["restAvailability"]["sources"] == []
    assert dumped["restAvailability"]["confidence"] == 1.0
    assert dumped["sqlAvailability"] == {"databaseName": ""}
    assert dumped["scimAvailability"]["status"] == ProtocolAvailability.UNKNOWN.value
    assert dumped["scimAvailability"]["baseApiEndpoint"] == []
    # Flat fields no longer live at the top level; they moved into the availability blocks.
    assert "baseApiEndpoint" not in dumped
    assert "databaseName" not in dumped


def test_info_metadata_serializes_rest_availability_for_rest():
    metadata = InfoMetadata(
        api_type=[ApiType.REST],
        rest_availability=RestAvailabilityInfo(
            status=ProtocolAvailability.PAID,
            required_plan="Enterprise",
            sources=[DetectionSource.DOCUMENTATION, DetectionSource.WEB_SEARCH],
        ),
    )
    dumped = metadata.model_dump(by_alias=True)

    assert dumped["restAvailability"]["status"] == ProtocolAvailability.PAID.value
    assert dumped["restAvailability"]["requiredPlan"] == "Enterprise"
    assert dumped["restAvailability"]["sources"] == ["documentation", "web_search"]
    assert dumped["restAvailability"]["confidence"] == 1.0


@pytest.mark.parametrize(
    ("detected_api_type", "block", "advisory", "expected_sources"),
    [
        (
            ApiType.REST,
            "restAvailability",
            {
                "rest_availability": RestAvailabilityInfo(
                    status=ProtocolAvailability.PAID,
                    required_plan="Enterprise",
                    sources=[DetectionSource.WEB_SEARCH],
                )
            },
            ["web_search"],
        ),
        (
            ApiType.SCIM,
            "scimAvailability",
            {
                "scim_availability": ScimAvailabilityInfo(
                    status=ProtocolAvailability.PAID,
                    required_plan="Enterprise",
                    sources=[DetectionSource.SCIM_CLOUD],
                )
            },
            ["scim_cloud"],
        ),
    ],
    ids=["rest", "scim"],
)
def test_merge_info_metadata_exposes_the_advisory_of_a_detected_protocol(
    detected_api_type: ApiType,
    block: str,
    advisory: dict,
    expected_sources: list[str],
):
    merged = merge_info_metadata(
        [InfoMetadataExtraction(name="Acme")],
        total_items=1,
        api_types=[detected_api_type],
        **advisory,
    )

    availability = merged["infoMetadata"][block]
    assert availability["status"] == ProtocolAvailability.PAID.value
    assert availability["requiredPlan"] == "Enterprise"
    assert availability["sources"] == expected_sources


@pytest.mark.parametrize(
    ("detected_api_type", "block", "advisory"),
    [
        (
            ApiType.SCIM,
            "restAvailability",
            {"rest_availability": RestAvailabilityInfo(status=ProtocolAvailability.PAID)},
        ),
        (
            ApiType.REST,
            "scimAvailability",
            {"scim_availability": ScimAvailabilityInfo(status=ProtocolAvailability.PAID)},
        ),
    ],
    ids=["rest-advisory-without-rest", "scim-advisory-without-scim"],
)
def test_merge_info_metadata_does_not_leak_the_advisory_of_an_undetected_protocol(
    detected_api_type: ApiType,
    block: str,
    advisory: dict,
):
    """The block stays present but empty, so a client never reads a stale protocol advisory."""
    merged = merge_info_metadata(
        [InfoMetadataExtraction(name="Acme")],
        total_items=1,
        api_types=[detected_api_type],
        **advisory,
    )

    availability = merged["infoMetadata"][block]
    assert availability["status"] == ProtocolAvailability.UNKNOWN.value
    assert availability["baseApiEndpoint"] == []


def test_merge_info_metadata_keeps_signal_scim_without_documents():
    # The documentation-free signals do not need chunks: a SCIM confirmation must survive a
    # zero-document merge instead of being discarded with an empty payload.
    merged = merge_info_metadata(
        [],
        total_items=0,
        api_types=[ApiType.SCIM],
        scim_availability=ScimAvailabilityInfo(
            status=ProtocolAvailability.PAID, required_plan="Enterprise", sources=[DetectionSource.WEB_SEARCH]
        ),
    )

    assert merged["infoMetadata"]["apiType"] == [ApiType.SCIM.value]
    assert merged["infoMetadata"]["scimAvailability"]["status"] == ProtocolAvailability.PAID.value
    assert merged["infoMetadata"]["scimAvailability"]["sources"] == ["web_search"]


def test_merge_info_metadata_without_documents_or_signals_is_null():
    # No documents and no signal-derived apiType still collapses to infoMetadata=null.
    merged = merge_info_metadata([], total_items=0, api_types=[])

    assert merged == {"infoMetadata": None}


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
