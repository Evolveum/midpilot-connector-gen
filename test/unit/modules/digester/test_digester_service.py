# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from src.modules.digester import service
from src.modules.digester.schema import (
    AttributeInfo,
    AuthInfo,
    BaseAPIEndpoint,
    EndpointInfo,
    InfoMetadata,
    ObjectClass,
    RelationRecord,
)


# ==================== EXTRACT OBJECT CLASSES ====================
@pytest.mark.asyncio
async def test_extract_object_classes_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extracting object classes from multiple documentation items.
    Validates metadata tracking, deduplication, and class-to-chunk mapping.
    """
    doc_uuid1 = uuid4()
    doc_uuid2 = uuid4()

    fake_doc_items = [
        {
            "uuid": str(doc_uuid1),
            "content": "User management API documentation",
            "summary": "User API docs",
            "@metadata": {"source": "api_spec", "category": "reference_api"},
        },
        {
            "uuid": str(doc_uuid2),
            "content": "Group management API documentation",
            "summary": "Group API docs",
            "@metadata": {"source": "api_spec", "category": "reference_api"},
        },
    ]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_object_classes") as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently") as mock_parallel,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_parallel.return_value = [
            (
                [
                    ObjectClass(
                        name="User",
                        relevant="true",
                        superclass=None,
                        abstract=False,
                        embedded=False,
                        description="Represents a user in the system",
                        relevant_documentations=[{"doc_id": str(doc_uuid1), "chunk_id": str(doc_uuid1)}],
                    ),
                ],
                True,
                doc_uuid1,
            ),
            (
                [
                    ObjectClass(
                        name="Group",
                        relevant="true",
                        superclass=None,
                        abstract=False,
                        embedded=False,
                        description="Represents a group of users",
                        relevant_documentations=[{"doc_id": str(doc_uuid2), "chunk_id": str(doc_uuid2)}],
                    ),
                ],
                True,
                doc_uuid2,
            ),
        ]

        class FakeDeduped:
            def model_dump(self, by_alias=True):
                return {
                    "objectClasses": [
                        {
                            "name": "User",
                            "relevant": "true",
                            "description": "Represents a user in the system",
                            "relevantDocumentations": [{"docId": str(doc_uuid1), "chunkId": str(doc_uuid1)}],
                        },
                        {
                            "name": "Group",
                            "relevant": "true",
                            "description": "Represents a group of users",
                            "relevantDocumentations": [{"docId": str(doc_uuid2), "chunkId": str(doc_uuid2)}],
                        },
                    ]
                }

        mock_dedupe.return_value = FakeDeduped()

        job_id = uuid4()
        session_id = uuid4()
        result = await service.extract_object_classes(fake_doc_items, True, "high", job_id, session_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "objectClasses" in result["result"]
        assert len(result["result"]["objectClasses"]) == 2
        assert result["result"]["objectClasses"][0]["name"] == "User"
        assert result["result"]["objectClasses"][1]["name"] == "Group"

        mock_parallel.assert_called_once()


@pytest.mark.asyncio
async def test_extract_object_classes_empty_docs(mock_llm, mock_digester_update_job_progress):
    """Test extract_object_classes with no documentation items."""
    with (
        patch("src.modules.digester.service.deduplicate_and_sort_object_classes") as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently") as mock_parallel,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_parallel.return_value = []

        class EmptyDeduped:
            def model_dump(self, by_alias=True):
                return {"objectClasses": []}

        mock_dedupe.return_value = EmptyDeduped()

        result = await service.extract_object_classes([], True, "high", uuid4(), uuid4())

        assert result["result"]["objectClasses"] == []
        assert result["relevantDocumentations"] == []


# ==================== EXTRACT ATTRIBUTES ====================
@pytest.mark.asyncio
async def test_extract_attributes_updates_session_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extract_attributes successfully extracts attributes and updates the session.
    Validates chunk selection, attribute extraction, and session update.
    """
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())

    fake_doc_items = [
        {
            "uuid": doc_uuid,
            "content": "User schema documentation",
            "summary": "User attributes",
            "@metadata": {"source": "schema"},
        }
    ]

    relevant_chunks = [
        {"doc_id": doc_uuid, "chunk_id": doc_uuid},
    ]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_attributes") as mock_extract_attrs,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (
            ["chunk-0 text", "chunk-2 text"],
            [(0, doc_uuid), (2, doc_uuid)],
        )

        mock_extract_attrs.return_value = {
            "result": {
                "attributes": {
                    "id": AttributeInfo(
                        type="string",
                        description="Unique identifier",
                        mandatory=True,
                        readable=True,
                        updatable=False,
                        creatable=True,
                        multivalue=False,
                        returnedByDefault=True,
                    ).model_dump(),
                    "username": AttributeInfo(
                        type="string",
                        description="User login name",
                        mandatory=True,
                        readable=True,
                        updatable=True,
                        creatable=True,
                        multivalue=False,
                        returnedByDefault=True,
                    ).model_dump(),
                }
            },
            "relevantDocumentations": relevant_chunks,
        }

        result = await service.extract_attributes(fake_doc_items, "User", session_id, relevant_chunks, job_id)

        # Verify result structure
        assert "result" in result
        assert "attributes" in result["result"]
        assert "id" in result["result"]["attributes"]
        assert "username" in result["result"]["attributes"]

        # Verify chunk extraction was called correctly
        mock_extract_chunks.assert_called_once_with(fake_doc_items, relevant_chunks, "Digester:Attributes")

        # Verify attribute extraction was called
        mock_extract_attrs.assert_called_once()
        mock_update_object_class.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_attributes_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks:
        mock_extract_chunks.return_value = ([], [])

        result = await service.extract_attributes([], "User", session_id, [], job_id)

        assert result["result"]["attributes"] == {}
        assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_attributes_session_not_found(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes handles missing session gracefully."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())

    fake_doc_items = [{"uuid": doc_uuid, "content": "test"}]
    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_attributes") as mock_extract_attrs,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk text"], [(0, doc_uuid)])
        mock_extract_attrs.return_value = {"result": {"attributes": {"id": {}}}, "relevantDocumentations": []}

        result = await service.extract_attributes(fake_doc_items, "User", session_id, relevant_chunks, job_id)

        # Should return result even if session update fails
        assert "result" in result
        mock_update_object_class.assert_awaited_once()


# ==================== EXTRACT ENDPOINTS ====================
@pytest.mark.asyncio
async def test_extract_endpoints_updates_session_success(mock_llm, mock_digester_update_job_progress):
    """
    Test extract_endpoints successfully extracts endpoints and updates the session.
    Validates chunk selection, endpoint extraction, and session update.
    """
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())
    base_api_url = "https://api.example.com"

    fake_doc_items = [
        {
            "uuid": doc_uuid,
            "content": "User endpoints documentation",
            "summary": "User API endpoints",
            "@metadata": {"source": "api_spec"},
        }
    ]

    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_endpoints") as mock_extract_endpoints,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk-0 text"], [(0, doc_uuid)])

        mock_extract_endpoints.return_value = {
            "result": {
                "endpoints": [
                    EndpointInfo(
                        method="GET",
                        path="/users",
                        description="List all users",
                        suggested_use=["getAll"],
                    ).model_dump(),
                    EndpointInfo(
                        method="POST",
                        path="/users",
                        description="Create a new user",
                        suggested_use=["create"],
                    ).model_dump(),
                    EndpointInfo(
                        method="GET",
                        path="/users/{id}",
                        description="Get user by ID",
                        suggested_use=["getById"],
                    ).model_dump(),
                ]
            },
            "relevantDocumentations": relevant_chunks,
        }

        result = await service.extract_endpoints(
            fake_doc_items, "User", session_id, relevant_chunks, job_id, base_api_url
        )

        # Verify result structure
        assert "result" in result
        assert "endpoints" in result["result"]
        assert len(result["result"]["endpoints"]) == 3
        assert result["result"]["endpoints"][0]["path"] == "/users"
        assert result["result"]["endpoints"][0]["method"] == "GET"

        # Verify chunk extraction was called
        mock_extract_chunks.assert_called_once_with(fake_doc_items, relevant_chunks, "Digester:Endpoints")

        # Verify endpoint extraction was called with base_api_url
        mock_extract_endpoints.assert_called_once()
        call_args = mock_extract_endpoints.call_args
        assert call_args[0][3] == base_api_url
        mock_update_object_class.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_endpoints_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = ([], [])

        result = await service.extract_endpoints([], "User", session_id, [], job_id, "")

        assert result["result"]["endpoints"] == []
        assert result["relevantDocumentations"] == []


@pytest.mark.asyncio
async def test_extract_endpoints_with_base_url(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints properly passes base_api_url to extraction function."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())
    base_api_url = "https://custom-api.example.com/v2"

    fake_doc_items = [{"uuid": doc_uuid, "content": "test", "summary": "", "@metadata": {}}]
    relevant_chunks = [{"doc_id": doc_uuid, "chunk_id": doc_uuid}]

    with (
        patch("src.modules.digester.service.select_doc_chunks") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_rest_endpoints") as mock_extract_endpoints,
        patch(
            "src.modules.digester.service.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_update_object_class,
        patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
    ):
        mock_extract_chunks.return_value = (["chunk"], [(0, doc_uuid)])
        mock_extract_endpoints.return_value = {"result": {"endpoints": []}, "relevantDocumentations": []}

        await service.extract_endpoints(fake_doc_items, "User", session_id, relevant_chunks, job_id, base_api_url)

        # Verify base_api_url was passed correctly
        call_args = mock_extract_endpoints.call_args
        assert call_args[0][3] == base_api_url
        mock_update_object_class.assert_awaited_once()


# ==================== EXTRACT AUTH ====================
@pytest.mark.asyncio
async def test_extract_auth_success(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = str(uuid4())
    doc_uuid2 = str(uuid4())

    fake_doc_items = [
        {
            "uuid": doc_uuid1,
            "content": "OAuth2 authentication documentation",
            "summary": "OAuth2 setup",
            "@metadata": {"source": "auth_guide"},
        },
        {
            "uuid": doc_uuid2,
            "content": "API Key authentication documentation",
            "summary": "API Key usage",
            "@metadata": {"source": "api_spec"},
        },
    ]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_auth", new_callable=AsyncMock) as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [
            (
                [AuthInfo(name="OAuth2", type="oauth2", quirks="Supports authorization_code and client_credentials")],
                True,
                doc_uuid1,
            ),
            (
                [AuthInfo(name="API Key", type="apiKey", quirks="Header: X-API-Key")],
                True,
                doc_uuid2,
            ),
        ]

        class FakeDedupedAuth:
            def model_dump(self, **kwargs):
                return {
                    "auth": [
                        {
                            "name": "OAuth2",
                            "type": "oauth2",
                            "quirks": "Supports authorization_code and client_credentials",
                        },
                        {"name": "API Key", "type": "apiKey", "quirks": "Header: X-API-Key"},
                    ]
                }

        mock_dedupe.return_value = FakeDedupedAuth()

        job_id = uuid4()
        result = await service.extract_auth(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result
        assert "auth" in result["result"]
        assert len(result["result"]["auth"]) == 2
        assert result["result"]["auth"][0]["name"] == "OAuth2"
        assert result["result"]["auth"][1]["name"] == "API Key"

        mock_parallel.assert_awaited_once()
        mock_dedupe.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_auth_empty_result(mock_llm, mock_digester_update_job_progress):
    """Test extract_auth when no authentication methods are found."""
    doc_uuid = str(uuid4())
    fake_doc_items = [{"uuid": doc_uuid, "content": "General documentation", "summary": "", "@metadata": {}}]

    with (
        patch("src.modules.digester.service.deduplicate_and_sort_auth", new_callable=AsyncMock) as mock_dedupe,
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [([], False, doc_uuid)]

        class EmptyAuth:
            def model_dump(self, **kwargs):
                return {"auth": []}

        mock_dedupe.return_value = EmptyAuth()

        result = await service.extract_auth(fake_doc_items, uuid4())

        assert result["result"]["auth"] == []
        mock_parallel.assert_awaited_once()
        mock_dedupe.assert_awaited_once()


# ==================== EXTRACT INFO METADATA ====================
@pytest.mark.asyncio
async def test_extract_info_metadata_success(mock_llm, mock_digester_update_job_progress):
    doc_uuid1 = uuid4()
    doc_uuid2 = uuid4()

    fake_doc_items = [
        {"uuid": str(doc_uuid1), "content": "API Overview: ExampleAPI v1.0"},
        {"uuid": str(doc_uuid2), "content": "Base URL: https://api.example.com/v1"},
    ]

    with (
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [
            (
                [
                    InfoMetadata(
                        name="ExampleAPI",
                        api_version="v1.0",
                        application_version="1.0.0",
                        api_type=["REST", "SCIM"],
                        base_api_endpoint=[],
                    )
                ],
                True,
                doc_uuid1,
            ),
            (
                [
                    InfoMetadata(
                        name="ExampleAPI",
                        api_version="v1.0",
                        application_version="1.0.0",
                        api_type=["REST", "SCIM"],
                        base_api_endpoint=[BaseAPIEndpoint(uri="https://api.example.com/v1", type="constant")],
                    )
                ],
                True,
                doc_uuid2,
            ),
        ]

        job_id = uuid4()
        result = await service.extract_info_metadata(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantDocumentations" in result

        metadata = result["result"]["infoMetadata"]
        assert metadata["name"] == "ExampleAPI"
        assert metadata["apiVersion"] == "v1.0"
        assert len(metadata["baseApiEndpoint"]) == 1

        mock_parallel.assert_awaited_once()


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
        patch("src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_extract.side_effect = [
            (
                [
                    InfoMetadata(
                        name="ExampleAPI",
                        api_version="1",
                        application_version="1.0.0",
                        api_type=["REST"],
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
                        api_type=["REST", "SCIM"],
                        base_api_endpoint=[],
                    )
                ],
                True,
            ),
        ]

        async def run_extractor_for_docs(*, chunk_items, job_id, extractor, logger_scope):
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


# ==================== INTEGRATION SCENARIOS ====================
@pytest.mark.asyncio
async def test_full_workflow_object_class_to_endpoints(mock_llm, mock_digester_update_job_progress):
    """
    Integration test simulating the full workflow:
    1. Extract object classes
    2. Extract attributes for a class
    3. Extract endpoints for a class
    """
    session_id = uuid4()
    doc_uuid = uuid4()

    doc_items = [
        {
            "uuid": str(doc_uuid),
            "content": "Complete API documentation with User schema and endpoints",
            "summary": "Full API docs",
            "@metadata": {"tags": "spec"},
        }
    ]

    with (
        patch("src.modules.digester.service.update_job_progress", new_callable=AsyncMock),
    ):
        # Step 1: Extract object classes
        with (
            patch(
                "src.modules.digester.service._run_doc_extractors_concurrently", new_callable=AsyncMock
            ) as mock_parallel,
            patch(
                "src.modules.digester.service.deduplicate_and_sort_object_classes", new_callable=AsyncMock
            ) as mock_dedupe_classes,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_parallel.return_value = [
                (
                    [
                        ObjectClass(
                            name="User",
                            relevant="true",
                            description="User entity",
                            relevant_documentations=[{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}],
                        )
                    ],
                    True,
                    doc_uuid,
                )
            ]

            class ObjectClassResult:
                def model_dump(self, by_alias=True):
                    return {
                        "objectClasses": [
                            {
                                "name": "User",
                                "relevant": "true",
                                "description": "User entity",
                                "relevantDocumentations": [{"docId": str(doc_uuid), "chunkId": str(doc_uuid)}],
                            }
                        ]
                    }

            mock_dedupe_classes.return_value = ObjectClassResult()

            classes_result = await service.extract_object_classes(doc_items, True, "high", uuid4(), session_id)
            assert len(classes_result["result"]["objectClasses"]) == 1

            mock_parallel.assert_awaited_once()
            mock_dedupe_classes.assert_awaited_once()

        # Step 2: Extract attributes
        with (
            patch("src.modules.digester.service.select_doc_chunks") as mock_chunks,
            patch("src.modules.digester.service._extract_rest_attributes") as mock_attrs,
            patch(
                "src.modules.digester.service.update_object_class_field_in_session",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_object_class,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_attrs.return_value = {
                "result": {"attributes": {"id": {"type": "string", "description": "ID"}}},
                "relevantDocumentations": [],
            }

            attrs_result = await service.extract_attributes(
                doc_items, "User", session_id, [{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}], uuid4()
            )
            assert "id" in attrs_result["result"]["attributes"]
            mock_update_object_class.assert_awaited_once()

        # Step 3: Extract endpoints
        with (
            patch("src.modules.digester.service.select_doc_chunks") as mock_chunks,
            patch("src.modules.digester.service._extract_rest_endpoints") as mock_endpoints,
            patch(
                "src.modules.digester.service.update_object_class_field_in_session",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_update_object_class,
            patch("src.modules.digester.service.get_session_api_types", new_callable=AsyncMock, return_value=[]),
        ):
            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_endpoints.return_value = {
                "result": {"endpoints": [{"method": "GET", "path": "/users", "description": "Get users"}]},
                "relevantDocumentations": [],
            }

            endpoints_result = await service.extract_endpoints(
                doc_items,
                "User",
                session_id,
                [{"doc_id": str(doc_uuid), "chunk_id": str(doc_uuid)}],
                uuid4(),
                "https://api.example.com",
            )
            assert len(endpoints_result["result"]["endpoints"]) == 1
            mock_update_object_class.assert_awaited_once()
