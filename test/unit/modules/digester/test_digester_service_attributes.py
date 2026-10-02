# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.documents.selection import DocumentationSelection, SelectionRole
from src.modules.digester.aggregation.merges import merge_attribute_candidates
from src.modules.digester.extractors.attributes import extract_attributes
from src.modules.digester.extractors.scim.baseline import ScimBaselineBundle
from src.modules.digester.schemas import (
    AttributeInfoRest,
    DocProcessingSequenceItem,
    ValidatedAttributeCandidate,
)
from src.shared.enums import ApiType


# ==================== EXTRACT ATTRIBUTES ====================
@pytest.mark.asyncio
async def test_merge_attribute_candidates_reuses_validated_sequence_text(mock_digester_update_job_progress):
    chunk_id = str(uuid4())
    doc_id = str(uuid4())
    attribute = ValidatedAttributeCandidate(
        name="email",
        description="User email address",
        relevant_sequences=[
            DocProcessingSequenceItem(
                chunk_id=chunk_id,
                start_sequence="email",
                end_sequence="string",
                text="email is a string identifier for the user",
            )
        ],
    )

    with patch(
        "src.modules.digester.extraction.sequences.extract_sequence",
        new_callable=AsyncMock,
    ) as mock_extract_sequence:
        result = await merge_attribute_candidates(
            object_class="User",
            attribute_objects=[attribute],
            job_id=uuid4(),
            build_dedup_chain=lambda: None,
            chunk_id_doc_id_map={chunk_id: doc_id},
        )

    assert len(result) == 1
    assert result[0].relevant_sequences[0].text == "email is a string identifier for the user"
    assert result[0].relevant_documentations == [{"chunk_id": chunk_id, "doc_id": doc_id}]
    mock_extract_sequence.assert_not_awaited()


def _doc(content: str, *, tags: list[str] | None = None, content_type: str | None = None) -> dict:
    metadata: dict = {"tags": tags or []}
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


def _selection(*, primary=(), fallback=(), scim_baseline=(), sql_schema=()) -> DocumentationSelection:
    corpus = [*primary, *fallback, *scim_baseline, *sql_schema]
    unique = list({item["chunkId"]: item for item in corpus}.values())
    return DocumentationSelection.from_corpus(
        unique,
        {
            SelectionRole.PRIMARY: list(primary),
            SelectionRole.FALLBACK: list(fallback),
            SelectionRole.SCIM_BASELINE: list(scim_baseline),
            SelectionRole.SQL_SCHEMA: list(sql_schema),
        },
    )


def _attributes_result(*names: str, doc: dict | None = None) -> dict:
    refs = [{"doc_id": doc["docId"], "chunk_id": doc["chunkId"]}] if doc else []
    return {
        "result": {
            "attributes": {
                name: AttributeInfoRest(type="string", description=name, relevant_sequences=[]).model_dump()
                for name in names
            }
        },
        "relevantDocumentations": refs,
    }


_EMPTY = {"result": {"attributes": {}}, "relevantDocumentations": []}


@pytest.mark.asyncio
async def test_extract_attributes_reads_primary_chunks_from_the_stored_selection(mock_digester_update_job_progress):
    primary = _doc("User schema documentation", tags=["user"])
    fallback = _doc("Unrelated reference")

    with (
        patch(
            "src.modules.digester.extractors.attributes._extract_rest_attributes",
            new_callable=AsyncMock,
            return_value=_attributes_result("id", "username", doc=primary),
        ) as mock_rest,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_persist,
    ):
        result = await extract_attributes(
            _selection(primary=[primary], fallback=[fallback]), "User", uuid4(), uuid4(), ApiType.REST
        )

    assert set(result["result"]["attributes"]) == {"id", "username"}
    mock_rest.assert_awaited_once()
    contents, object_class, _, chunk_ids, metadata_map, id_map = mock_rest.await_args.args
    assert (contents, object_class, chunk_ids) == ([primary["content"]], "User", [primary["chunkId"]])
    assert metadata_map == {primary["chunkId"]: {"summary": primary["summary"], "@metadata": {"tags": ["user"]}}}
    assert id_map == {primary["chunkId"]: primary["docId"]}
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_attributes_without_primary_chunks_returns_empty_result(mock_digester_update_job_progress):
    with (
        patch("src.modules.digester.extractors.attributes._extract_rest_attributes", new_callable=AsyncMock) as rest,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session", new_callable=AsyncMock
        ) as persist,
    ):
        result = await extract_attributes(_selection(), "User", uuid4(), uuid4(), ApiType.REST)

    assert result["result"]["attributes"] == {}
    assert result["relevantDocumentations"] == []
    rest.assert_not_awaited()
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_attributes_retries_once_over_stored_fallback_chunks(mock_digester_update_job_progress):
    primary = _doc("Overview without attributes", tags=["user"])
    fallback = _doc("Fallback reference with attributes")
    fallback_result = _attributes_result("email", doc=fallback)

    with (
        patch(
            "src.modules.digester.extractors.attributes._extract_rest_attributes",
            new_callable=AsyncMock,
            side_effect=[_EMPTY, fallback_result],
        ) as mock_rest,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await extract_attributes(
            _selection(primary=[primary], fallback=[fallback]), "User", uuid4(), uuid4(), ApiType.REST
        )

    assert result == fallback_result
    assert [call.args[0] for call in mock_rest.await_args_list] == [[primary["content"]], [fallback["content"]]]


@pytest.mark.asyncio
async def test_extract_attributes_does_not_retry_when_primary_found_attributes(mock_digester_update_job_progress):
    primary = _doc("User schema", tags=["user"])
    with (
        patch(
            "src.modules.digester.extractors.attributes._extract_rest_attributes",
            new_callable=AsyncMock,
            return_value=_attributes_result("id", doc=primary),
        ) as mock_rest,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await extract_attributes(
            _selection(primary=[primary], fallback=[_doc("Fallback")]), "User", uuid4(), uuid4(), ApiType.REST
        )

    mock_rest.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_attributes_without_stored_fallback_keeps_empty_result(mock_digester_update_job_progress):
    primary = _doc("Overview without attributes", tags=["user"])
    with (
        patch(
            "src.modules.digester.extractors.attributes._extract_rest_attributes",
            new_callable=AsyncMock,
            return_value=_EMPTY,
        ) as mock_rest,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_persist,
    ):
        result = await extract_attributes(_selection(primary=[primary]), "User", uuid4(), uuid4(), ApiType.REST)

    assert result == _EMPTY
    mock_rest.assert_awaited_once()
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_scim_extract_attributes_uses_heuristics_then_fallback_with_the_stored_baseline(
    mock_digester_update_job_progress,
):
    conndev = _doc(
        '{"schemaContent": "{\\"id\\": \\"urn:ietf:params:scim:schemas:core:2.0:User\\", \\"name\\": \\"User\\", '
        '\\"attributes\\": []}"}',
        tags=["scim", "schema", "conndev"],
        content_type="application/com.evolveum.conndev+json",
    )
    fallback = _doc("Slack maps primary email to emails[0].value.", tags=["scim", "attributes"])
    retry_result = _attributes_result("Primary Email", doc=fallback)

    with (
        patch(
            "src.modules.digester.extractors.attributes.extract_scim_attributes",
            new_callable=AsyncMock,
            side_effect=[_EMPTY, retry_result],
        ) as mock_scim,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_persist,
    ):
        result = await extract_attributes(
            _selection(fallback=[fallback], scim_baseline=[conndev]), "UserEmails", uuid4(), uuid4(), ApiType.SCIM
        )

    assert result == retry_result
    first, retry = (call.args for call in mock_scim.await_args_list)
    # SCIM tolerates no primary documentation: schema heuristics run on the stored baseline.
    assert first[0] == [] and first[4] == []
    assert isinstance(first[3], ScimBaselineBundle)
    assert "User" in first[3].schemas
    assert retry[0] == [fallback["content"]]
    assert retry[3] is first[3]
    assert retry[4] == [fallback["chunkId"]]
    assert retry[6] == {fallback["chunkId"]: fallback["docId"]}
    mock_persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_sql_extract_attributes_reads_the_stored_schema_documentation(mock_digester_update_job_progress):
    table = _doc("CREATE TABLE users (id UUID PRIMARY KEY);", content_type="text/sql")
    other = _doc("Overview")

    with (
        patch(
            "src.modules.digester.extractors.attributes.extract_sql_attributes",
            new_callable=AsyncMock,
            return_value=_attributes_result("id", doc=table),
        ) as mock_sql,
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        await extract_attributes(_selection(sql_schema=[table, other]), "User", uuid4(), uuid4(), ApiType.SQL)

    doc_items = mock_sql.await_args.args[0]
    assert [item["chunkId"] for item in doc_items] == [table["chunkId"], other["chunkId"]]


@pytest.mark.asyncio
async def test_extract_attributes_returns_result_when_session_write_back_is_skipped(mock_digester_update_job_progress):
    primary = _doc("User schema", tags=["user"])
    with (
        patch(
            "src.modules.digester.extractors.attributes._extract_rest_attributes",
            new_callable=AsyncMock,
            return_value=_attributes_result("id", doc=primary),
        ),
        patch(
            "src.modules.digester.persistence.update_object_class_field_in_session",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_persist,
    ):
        result = await extract_attributes(_selection(primary=[primary]), "User", uuid4(), uuid4(), ApiType.REST)

    assert "id" in result["result"]["attributes"]
    mock_persist.assert_awaited_once()
