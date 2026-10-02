# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.modules.digester.aggregation.merges import merge_attribute_candidates
from src.modules.digester.extraction.chunk_extraction import extract_single_chunk
from src.modules.digester.schemas import (
    AttributeDedupResponse,
    AttributeDiscoveryResponse,
    DiscoveryAttribute,
    DocProcessingSequenceItem,
    DocSequenceMarker,
    ValidatedAttributeCandidate,
)


def test_discovery_schema_only_requests_markers():
    schema = AttributeDiscoveryResponse.model_json_schema()
    assert set(schema["$defs"]["DocSequenceMarker"]["properties"]) == {"startSequence", "endSequence"}
    assert "ValidatedAttributeCandidate" not in schema.get("$defs", {})


@pytest.mark.parametrize("missing", ["chunk_id", "text"])
def test_validated_candidate_requires_evidence_details(missing):
    sequence = {"chunk_id": "chunk", "start_sequence": "start", "end_sequence": "end", "text": "start end"}
    del sequence[missing]
    with pytest.raises(ValidationError):
        ValidatedAttributeCandidate.model_validate({"name": "email", "relevant_sequences": [sequence]})


def test_validated_candidate_requires_sequences():
    with pytest.raises(ValidationError):
        ValidatedAttributeCandidate(name="email", relevant_sequences=[])


@pytest.mark.asyncio
@pytest.mark.parametrize("include_valid", [True, False])
async def test_extraction_validates_new_candidate_before_merge(include_valid):
    chunk_id, job_id = uuid4(), uuid4()
    invalid = DocSequenceMarker(start_sequence="Unrelated missing heading", end_sequence="Absent final marker")
    markers = [invalid]
    if include_valid:
        markers.append(DocSequenceMarker(start_sequence="User object", end_sequence="email string"))
    raw = DiscoveryAttribute(name="email", description="Email address", relevant_sequences=markers)
    original = raw.model_dump()
    chain = AsyncMock()
    chain.ainvoke.return_value = AttributeDiscoveryResponse(
        attributes=[raw, DiscoveryAttribute(name="empty", relevant_sequences=[])]
    )

    with (
        patch("src.modules.digester.extraction.chunk_extraction.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.append_job_error", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.chunk_extraction.pool.require_process_pool", return_value=None),
        patch("src.modules.digester.aggregation.merges.update_job_progress", new_callable=AsyncMock),
        patch("src.modules.digester.extraction.sequences.extract_sequence", new_callable=AsyncMock) as reload_text,
    ):
        items, found = await extract_single_chunk(
            schema="Prefix. User object includes id string and email string. Suffix.",
            pydantic_model=AttributeDiscoveryResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda response: response.attributes,
            job_id=job_id,
            chunk_id=chunk_id,
            enabled_sequence_checking=True,
            validated_item_model=ValidatedAttributeCandidate,
            fuzzy_start_marker_error_ratio=0,
            fuzzy_end_marker_error_ratio=0,
            extraction_chain=chain,
        )
        assert found is include_valid
        assert raw.model_dump() == original
        assert all(isinstance(seq, DocSequenceMarker) for seq in raw.relevant_sequences)
        if include_valid:
            assert len(items) == 1
            assert isinstance(items[0], ValidatedAttributeCandidate)
            assert items[0] is not raw
            assert len(items[0].relevant_sequences) == 1
            sequence = items[0].relevant_sequences[0]
            assert sequence.chunk_id == str(chunk_id)
            assert sequence.text == "User object includes id string and email string"
            merged = await merge_attribute_candidates("User", items, job_id, lambda: None, {str(chunk_id): "doc"})
            assert merged[0].name == "email"
            assert merged[0].description == "Email address"
            assert merged[0].relevant_sequences == items[0].relevant_sequences
            assert merged[0].relevant_documentations == [{"chunk_id": str(chunk_id), "doc_id": "doc"}]
        else:
            assert items == []
        reload_text.assert_not_awaited()
    chain.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_validated_model_requires_sequence_checking():
    chain = AsyncMock()
    with pytest.raises(ValueError, match="requires enabled_sequence_checking"):
        await extract_single_chunk(
            schema="text",
            pydantic_model=AttributeDiscoveryResponse,
            system_prompt="system",
            user_prompt="user",
            parse_fn=lambda response: response.attributes,
            job_id=uuid4(),
            extraction_chain=chain,
            validated_item_model=ValidatedAttributeCandidate,
        )
    chain.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_preserves_heuristics_and_llm_plan():
    def candidate(name, chunk, description):
        return ValidatedAttributeCandidate(
            name=name,
            description=description,
            relevant_sequences=[
                DocProcessingSequenceItem(chunk_id=chunk, start_sequence="start", end_sequence="end", text="start end")
            ],
        )

    candidates = [
        candidate("email", "one", "Email"),
        candidate(" EMAIL ", "one", "Detailed email description"),
        candidate("Email", "two", "Email"),
        candidate("mail", "three", "Mail"),
        candidate("discard", "four", "Irrelevant"),
    ]
    with (
        patch("src.modules.digester.aggregation.merges.update_job_progress", new_callable=AsyncMock),
        patch(
            "src.modules.digester.aggregation.merges.invoke_llm",
            new_callable=AsyncMock,
            return_value=AttributeDedupResponse(duplicates=[("email", "mail")], to_be_deleted=["discard"]),
        ) as invoke,
    ):
        merged = await merge_attribute_candidates(
            "User", candidates, uuid4(), lambda: object(), {chunk: "doc" for chunk in ("one", "two", "three", "four")}
        )

    assert [item.name for item in merged] == ["email"]
    assert merged[0].description == "Detailed email description"
    assert [seq.chunk_id for seq in merged[0].relevant_sequences] == ["one", "two", "three"]
    assert len(candidates[0].relevant_sequences) == 1
    invoke.assert_awaited_once()
    submitted = json.loads(invoke.await_args.args[1]["attributes_list"])
    assert [item["name"] for item in submitted] == ["email", "mail", "discard"]
    assert submitted[0]["relevant_documentations"] == [
        {"chunkId": "one", "docId": "doc"},
        {"chunkId": "two", "docId": "doc"},
    ]
    assert merged[0].type is None
    assert merged[0].mandatory is None
