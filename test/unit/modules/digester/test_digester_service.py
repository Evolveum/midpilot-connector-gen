# Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

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
        patch("src.modules.digester.service.process_documents_in_parallel") as mock_parallel,
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
                        relevant_chunks=[{"docUuid": doc_uuid1}],
                    ),
                ],
                [0, 1],
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
                        relevant_chunks=[{"docUuid": doc_uuid2}],
                    ),
                ],
                [0],
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
                            "relevantChunks": [{"docUuid": doc_uuid1}],
                        },
                        {
                            "name": "Group",
                            "relevant": "true",
                            "description": "Represents a group of users",
                            "relevantChunks": [{"docUuid": doc_uuid2}],
                        },
                    ]
                }

        mock_dedupe.return_value = FakeDeduped()

        job_id = uuid4()
        result = await service.extract_object_classes(fake_doc_items, True, "high", job_id)

        assert "result" in result
        assert "relevantChunks" in result
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
        patch("src.modules.digester.utils.parallel_docs.process_documents_in_parallel") as mock_parallel,
    ):
        mock_parallel.return_value = []

        class EmptyDeduped:
            def model_dump(self, by_alias=True):
                return {"objectClasses": []}

        mock_dedupe.return_value = EmptyDeduped()

        result = await service.extract_object_classes([], True, "high", uuid4())

        assert result["result"]["objectClasses"] == []
        assert result["relevantChunks"] == []


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
        {"docUuid": doc_uuid},
    ]

    object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "description": "User object",
                "relevantChunks": relevant_chunks,
            }
        ]
    }

    mock_db_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_session_data = AsyncMock(return_value=object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_attributes") as mock_extract_attrs,
        patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
        patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
    ):
        # Setup mocks
        mock_session_maker.return_value.__aenter__.return_value = mock_db_session
        mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
        mock_repo_class.return_value = mock_repo

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
            "relevantChunks": relevant_chunks,
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

        # Verify session was updated
        mock_repo.update_session.assert_called_once()
        update_call_args = mock_repo.update_session.call_args
        assert update_call_args[0][0] == session_id
        updated_data = update_call_args[0][1]
        assert "objectClassesOutput" in updated_data


@pytest.mark.asyncio
async def test_extract_attributes_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks:
        mock_extract_chunks.return_value = ([], [])

        result = await service.extract_attributes([], "User", session_id, [], job_id)

        assert result["result"]["attributes"] == {}
        assert result["relevantChunks"] == []


@pytest.mark.asyncio
async def test_extract_attributes_session_not_found(mock_llm, mock_digester_update_job_progress):
    """Test extract_attributes handles missing session gracefully."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())

    fake_doc_items = [{"uuid": doc_uuid, "content": "test"}]
    relevant_chunks = [{"docUuid": doc_uuid}]

    mock_db_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_session_data = AsyncMock(return_value=None)

    with (
        patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_attributes") as mock_extract_attrs,
        patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
        patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
    ):
        mock_session_maker.return_value.__aenter__.return_value = mock_db_session
        mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
        mock_repo_class.return_value = mock_repo

        mock_extract_chunks.return_value = (["chunk text"], [(0, doc_uuid)])
        mock_extract_attrs.return_value = {"result": {"attributes": {"id": {}}}, "relevantChunks": []}

        result = await service.extract_attributes(fake_doc_items, "User", session_id, relevant_chunks, job_id)

        # Should return result even if session update fails
        assert "result" in result
        mock_repo.update_session.assert_not_called()


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

    relevant_chunks = [{"docUuid": doc_uuid}]

    object_classes_output = {
        "objectClasses": [
            {
                "name": "User",
                "relevant": "true",
                "description": "User object",
                "relevantChunks": relevant_chunks,
            }
        ]
    }

    mock_db_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_session_data = AsyncMock(return_value=object_classes_output)
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_endpoints") as mock_extract_endpoints,
        patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
        patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
    ):
        mock_session_maker.return_value.__aenter__.return_value = mock_db_session
        mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
        mock_repo_class.return_value = mock_repo

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
            "relevantChunks": relevant_chunks,
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

        # Verify session was updated
        mock_repo.update_session.assert_called_once()


@pytest.mark.asyncio
async def test_extract_endpoints_no_relevant_chunks(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints when no relevant chunks are found."""
    session_id = uuid4()
    job_id = uuid4()

    with patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks:
        mock_extract_chunks.return_value = ([], [])

        result = await service.extract_endpoints([], "User", session_id, [], job_id, "")

        assert result["result"]["endpoints"] == []
        assert result["relevantChunks"] == []


@pytest.mark.asyncio
async def test_extract_endpoints_with_base_url(mock_llm, mock_digester_update_job_progress):
    """Test extract_endpoints properly passes base_api_url to extraction function."""
    session_id = uuid4()
    job_id = uuid4()
    doc_uuid = str(uuid4())
    base_api_url = "https://custom-api.example.com/v2"

    fake_doc_items = [{"uuid": doc_uuid, "content": "test", "summary": "", "@metadata": {}}]
    relevant_chunks = [{"docUuid": doc_uuid}]

    mock_db_session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.get_session_data = AsyncMock(
        return_value={"objectClasses": [{"name": "User", "endpoints": [], "relevantChunks": relevant_chunks}]}
    )
    mock_repo.update_session = AsyncMock()

    with (
        patch("src.modules.digester.service._select_docs_by_uuid") as mock_extract_chunks,
        patch("src.modules.digester.service._extract_endpoints") as mock_extract_endpoints,
        patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
        patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
    ):
        mock_session_maker.return_value.__aenter__.return_value = mock_db_session
        mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
        mock_repo_class.return_value = mock_repo

        mock_extract_chunks.return_value = (["chunk"], [(0, doc_uuid)])
        mock_extract_endpoints.return_value = {"result": {"endpoints": []}, "relevantChunks": []}

        await service.extract_endpoints(fake_doc_items, "User", session_id, relevant_chunks, job_id, base_api_url)

        # Verify base_api_url was passed correctly
        call_args = mock_extract_endpoints.call_args
        assert call_args[0][3] == base_api_url


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
        patch("src.modules.digester.service.process_documents_in_parallel", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [
            (
                [AuthInfo(name="OAuth2", type="oauth2", quirks="Supports authorization_code and client_credentials")],
                [0, 1],
                doc_uuid1,
            ),
            (
                [AuthInfo(name="API Key", type="apiKey", quirks="Header: X-API-Key")],
                [0],
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
        assert "relevantChunks" in result
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
        patch("src.modules.digester.service.process_documents_in_parallel", new_callable=AsyncMock) as mock_parallel,
    ):
        mock_parallel.return_value = [([], [0], doc_uuid)]

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
        patch("src.modules.digester.service._extract_info_metadata", new_callable=AsyncMock) as mock_extract,
        patch("src.modules.digester.service.increment_processed_documents", new_callable=AsyncMock) as mock_increment,
    ):
        mock_extract.side_effect = [
            (
                InfoMetadata(
                    name="ExampleAPI",
                    api_version="v1.0",
                    application_version="1.0.0",
                    api_type=["REST", "OpenAPI"],
                    base_api_endpoint=[],
                ),
                [0],
            ),
            (
                InfoMetadata(
                    name="ExampleAPI",
                    api_version="v1.0",
                    application_version="1.0.0",
                    api_type=["REST", "OpenAPI"],
                    base_api_endpoint=[BaseAPIEndpoint(uri="https://api.example.com/v1", type="constant")],
                ),
                [0],
            ),
        ]

        job_id = uuid4()
        result = await service.extract_info_metadata(fake_doc_items, job_id)

        assert "result" in result
        assert "relevantChunks" in result

        metadata = result["result"]
        assert metadata["name"] == "ExampleAPI"
        assert metadata["apiVersion"] == "v1.0"
        assert len(metadata["baseApiEndpoint"]) == 1

        assert mock_extract.call_count == 2
        assert mock_increment.call_count == 2


@pytest.mark.asyncio
async def test_extract_info_metadata_empty_docs(mock_llm, mock_digester_update_job_progress):
    """Test extract_info_metadata with no documentation items."""
    with patch("src.modules.digester.service.update_job_progress"):
        result = await service.extract_info_metadata([], uuid4())

        assert result["result"] == {}
        assert result["relevantChunks"] == []


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
        patch("src.modules.digester.service._process_over_documents") as mock_process,
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
            "relevantChunks": [{"docUuid": str(doc_uuid)}],
        }

        job_id = uuid4()
        result = await service.extract_relations(fake_doc_items, relevant_object_class, job_id)

        assert "result" in result
        assert "relevantChunks" in result
        assert "relations" in result["result"]

        relation = result["result"]["relations"][0]
        assert relation["subject"] == "user"
        assert relation["object"] == "group"


@pytest.mark.asyncio
async def test_extract_relations_no_relations_found(mock_llm, mock_digester_update_job_progress):
    """Test extract_relations when no relations are discovered."""
    fake_doc_items = [{"uuid": str(uuid4()), "content": "No relations", "summary": "", "@metadata": {}}]

    with patch("src.modules.digester.service._process_over_documents") as mock_process:
        mock_process.return_value = {"result": {"relations": []}, "relevantChunks": []}

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
        patch("src.modules.digester.service.increment_processed_documents", new_callable=AsyncMock),
    ):
        # Step 1: Extract object classes
        with (
            patch(
                "src.modules.digester.service.process_documents_in_parallel", new_callable=AsyncMock
            ) as mock_parallel,
            patch(
                "src.modules.digester.service.deduplicate_and_sort_object_classes", new_callable=AsyncMock
            ) as mock_dedupe_classes,
        ):
            mock_parallel.return_value = [
                (
                    [
                        ObjectClass(
                            name="User",
                            relevant="true",
                            description="User entity",
                            relevant_chunks=[{"docUuid": doc_uuid}],
                        )
                    ],
                    [0],
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
                                "relevantChunks": [{"docUuid": str(doc_uuid)}],
                            }
                        ]
                    }

            mock_dedupe_classes.return_value = ObjectClassResult()

            classes_result = await service.extract_object_classes(doc_items, True, "high", uuid4())
            assert len(classes_result["result"]["objectClasses"]) == 1

            mock_parallel.assert_awaited_once()
            mock_dedupe_classes.assert_awaited_once()

        # Step 2: Extract attributes
        mock_db_session = AsyncMock()
        mock_repo = MagicMock()
        object_classes_output = classes_result["result"]
        mock_repo.get_session_data = AsyncMock(return_value=object_classes_output)
        mock_repo.update_session = AsyncMock()

        with (
            patch("src.modules.digester.service._select_docs_by_uuid") as mock_chunks,
            patch("src.modules.digester.service._extract_attributes") as mock_attrs,
            patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
            patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
        ):
            mock_session_maker.return_value.__aenter__.return_value = mock_db_session
            mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
            mock_repo_class.return_value = mock_repo

            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_attrs.return_value = {
                "result": {"attributes": {"id": {"type": "string", "description": "ID"}}},
                "relevantChunks": [],
            }

            attrs_result = await service.extract_attributes(
                doc_items, "User", session_id, [{"docUuid": str(doc_uuid)}], uuid4()
            )
            assert "id" in attrs_result["result"]["attributes"]

        # Step 3: Extract endpoints
        with (
            patch("src.modules.digester.service._select_docs_by_uuid") as mock_chunks,
            patch("src.modules.digester.service._extract_endpoints") as mock_endpoints,
            patch("src.modules.digester.service.async_session_maker") as mock_session_maker,
            patch("src.modules.digester.service.SessionRepository") as mock_repo_class,
        ):
            # Update mock repo to include attributes
            object_classes_output["objectClasses"][0]["attributes"] = attrs_result["result"]["attributes"]
            mock_repo.get_session_data = AsyncMock(return_value=object_classes_output)

            mock_session_maker.return_value.__aenter__.return_value = mock_db_session
            mock_session_maker.return_value.__aexit__.return_value = AsyncMock()
            mock_repo_class.return_value = mock_repo

            mock_chunks.return_value = (["chunk"], [(0, str(doc_uuid))])
            mock_endpoints.return_value = {
                "result": {"endpoints": [{"method": "GET", "path": "/users", "description": "Get users"}]},
                "relevantChunks": [],
            }

            endpoints_result = await service.extract_endpoints(
                doc_items, "User", session_id, [{"docUuid": str(doc_uuid)}], uuid4(), "https://api.example.com"
            )
            assert len(endpoints_result["result"]["endpoints"]) == 1
