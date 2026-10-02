# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Stored documentation selection: shape, validation, cache identity and cache remap."""

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.documents.selection import (
    SELECTED_CHUNK_METADATA_KEYS,
    DocumentationSelection,
    SelectionRole,
    build_selection_chunk_remap,
)
from src.jobs.payload import InvalidJobPayloadError, deserialize_call, job_input_reference, serialize_value
from src.shared.normalize import (
    DOCUMENTATION_SELECTION_INPUT_KEY,
    ORDER_SENSITIVE_SELECTION_ROLES,
    normalized_input_fingerprint,
)


def _item(content: str, *, url: str | None = None, **metadata: Any) -> dict[str, Any]:
    return {
        "chunkId": str(uuid4()),
        "docId": str(uuid4()),
        "url": url or f"https://docs.example.com/{content.split()[0].lower()}",
        "summary": f"summary: {content}",
        "content": content,
        "@metadata": {"category": "spec_yaml", "token_count": 12, "tags": ["user"], **metadata},
    }


def _copy_with_new_ids(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "chunkId": str(uuid4()), "docId": str(uuid4())}


def _input(selection: DocumentationSelection, **extra: Any) -> dict[str, Any]:
    return {
        "objectClass": "user",
        "apiType": "rest",
        DOCUMENTATION_SELECTION_INPUT_KEY: selection.to_job_input(),
        **extra,
    }


def test_chunks_are_stored_once_in_corpus_order_with_only_extraction_metadata():
    first = _item("User schema", content_type="application/yaml", filename="spec.yaml")
    second = _item("Group schema")
    unselected = _item("Pricing page")

    selection = DocumentationSelection.from_corpus(
        [first, unselected, second],
        {
            SelectionRole.PRIMARY: [{"doc_id": second["docId"], "chunk_id": second["chunkId"]}, first],
            SelectionRole.FALLBACK: [],
            SelectionRole.SCIM_BASELINE: [first],
        },
    )

    assert [chunk.chunk_id for chunk in selection.chunks] == [first["chunkId"], second["chunkId"]]
    assert [ref.chunk_id for ref in selection.primary] == [first["chunkId"], second["chunkId"]]
    assert selection.chunks[0].metadata == {"content_type": "application/yaml", "tags": ["user"]}
    assert selection.documentation_items(SelectionRole.SCIM_BASELINE)[0]["@metadata"]["content_type"] == (
        "application/yaml"
    )


def test_references_to_documentation_the_corpus_does_not_hold_are_dropped():
    present = _item("User schema")
    selection = DocumentationSelection.from_corpus(
        [present],
        {SelectionRole.PRIMARY: [present, {"docId": str(uuid4()), "chunkId": str(uuid4())}]},
    )

    assert selection.relevant_chunks(SelectionRole.PRIMARY) == [
        {"doc_id": present["docId"], "chunk_id": present["chunkId"]}
    ]


def _stored(chunk: dict[str, Any]) -> dict[str, Any]:
    return {"chunkId": chunk["chunkId"], "docId": chunk["docId"], "content": chunk["content"]}


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s, a, b: s.update(primary=[{"docId": a["docId"], "chunkId": str(uuid4())}]), id="dangling"),
        pytest.param(lambda s, a, b: s.update(primary=[{"docId": str(uuid4()), "chunkId": a["chunkId"]}]), id="doc"),
        pytest.param(lambda s, a, b: s.update(chunks=[_stored(a), _stored(a)]), id="duplicate-chunk"),
        pytest.param(lambda s, a, b: s.update(chunks=[_stored(a), _stored(b)]), id="unreferenced-chunk"),
        pytest.param(lambda s, a, b: s.update(fallback=[{"docId": a["docId"], "chunkId": a["chunkId"]}]), id="repeat"),
        pytest.param(lambda s, a, b: s.update(version=2), id="version"),
    ],
)
def test_an_inconsistent_stored_selection_is_rejected(mutate):
    first = _item("User schema")
    second = _item("Group schema")
    stored = DocumentationSelection.from_corpus([first], {SelectionRole.PRIMARY: [first]}).to_job_input()
    mutate(stored, first, second)

    with pytest.raises(ValidationError):
        DocumentationSelection.model_validate(stored)


async def _worker(selection: DocumentationSelection) -> None:
    raise AssertionError("must not run")


def test_an_invalid_stored_selection_fails_job_deserialization_before_the_worker_runs():
    first = _item("User schema")
    stored = DocumentationSelection.from_corpus([first], {SelectionRole.PRIMARY: [first]}).to_job_input()
    stored["primary"] = [{"docId": first["docId"], "chunkId": str(uuid4())}]
    raw_kwargs = serialize_value({"selection": job_input_reference(DOCUMENTATION_SELECTION_INPUT_KEY)})

    with pytest.raises(InvalidJobPayloadError):
        deserialize_call(_worker, [], raw_kwargs, job_input={DOCUMENTATION_SELECTION_INPUT_KEY: stored})


def test_a_valid_stored_selection_round_trips_through_job_deserialization():
    first = _item("User schema")
    selection = DocumentationSelection.from_corpus([first], {SelectionRole.PRIMARY: [first]})
    raw_kwargs = serialize_value({"selection": job_input_reference(DOCUMENTATION_SELECTION_INPUT_KEY)})

    args, _ = deserialize_call(
        _worker, [], raw_kwargs, job_input={DOCUMENTATION_SELECTION_INPUT_KEY: selection.to_job_input()}
    )

    assert args == (selection,)


def test_cache_identity_ignores_chunk_uuids_and_the_order_of_llm_attempt_chunks():
    primary = _item("User schema")
    fallback = _item("Group schema")
    original = DocumentationSelection.from_corpus(
        [primary, fallback], {SelectionRole.PRIMARY: [primary], SelectionRole.FALLBACK: [fallback]}
    )
    copied_primary, copied_fallback = _copy_with_new_ids(primary), _copy_with_new_ids(fallback)
    copy_in_other_order = DocumentationSelection.from_corpus(
        [copied_fallback, copied_primary],
        {SelectionRole.PRIMARY: [copied_primary], SelectionRole.FALLBACK: [copied_fallback]},
    )

    assert normalized_input_fingerprint(_input(original)) == normalized_input_fingerprint(_input(copy_in_other_order))


def test_documentation_outside_the_selection_does_not_change_the_cache_identity():
    primary = _item("User schema")
    small_corpus = DocumentationSelection.from_corpus([primary], {SelectionRole.PRIMARY: [primary]})
    larger_corpus = DocumentationSelection.from_corpus(
        [_item("Newly scraped pricing page"), primary], {SelectionRole.PRIMARY: [primary]}
    )

    assert normalized_input_fingerprint(_input(small_corpus)) == normalized_input_fingerprint(_input(larger_corpus))


@pytest.mark.parametrize("role", [SelectionRole.PRIMARY, SelectionRole.FALLBACK, SelectionRole.SCIM_BASELINE])
def test_changing_a_selected_chunk_changes_the_cache_identity(role):
    stable = _item("User schema")
    selected = _item("Selected documentation")
    changed = {**selected, "content": "Selected documentation, revised"}
    roles = {SelectionRole.PRIMARY: [stable]}

    before = DocumentationSelection.from_corpus([stable, selected], {**roles, role: [*roles.get(role, []), selected]})
    after = DocumentationSelection.from_corpus([stable, changed], {**roles, role: [*roles.get(role, []), changed]})

    assert normalized_input_fingerprint(_input(before)) != normalized_input_fingerprint(_input(after))


def test_moving_a_chunk_between_attempts_changes_the_cache_identity():
    first = _item("User schema")
    second = _item("Group schema")
    as_fallback = DocumentationSelection.from_corpus(
        [first, second], {SelectionRole.PRIMARY: [first], SelectionRole.FALLBACK: [second]}
    )
    as_primary = DocumentationSelection.from_corpus([first, second], {SelectionRole.PRIMARY: [first, second]})

    assert normalized_input_fingerprint(_input(as_fallback)) != normalized_input_fingerprint(_input(as_primary))


def test_protocol_and_selection_version_are_part_of_the_cache_identity():
    primary = _item("User schema")
    selection = DocumentationSelection.from_corpus([primary], {SelectionRole.PRIMARY: [primary]})
    other_version = _input(selection)
    other_version[DOCUMENTATION_SELECTION_INPUT_KEY] = {
        **other_version[DOCUMENTATION_SELECTION_INPUT_KEY],
        "version": 0,
    }

    assert normalized_input_fingerprint(_input(selection)) != normalized_input_fingerprint(
        _input(selection, apiType="scim")
    )
    assert normalized_input_fingerprint(_input(selection)) != normalized_input_fingerprint(other_version)


def test_remap_pairs_every_chunk_including_identical_duplicates():
    primary = _item("User schema")
    duplicate_a = _item("Repeated paragraph", url="https://docs.example.com/repeated")
    duplicate_b = {**_copy_with_new_ids(duplicate_a)}
    baseline = _item("{}", content_type="application/conndev+json")
    previous = DocumentationSelection.from_corpus(
        [primary, duplicate_a, duplicate_b, baseline],
        {
            SelectionRole.PRIMARY: [primary],
            SelectionRole.FALLBACK: [duplicate_a, duplicate_b],
            SelectionRole.SCIM_BASELINE: [baseline],
        },
    )
    current_items = [_copy_with_new_ids(item) for item in (primary, duplicate_a, duplicate_b, baseline)]
    current = DocumentationSelection.from_corpus(
        current_items,
        {
            SelectionRole.PRIMARY: [current_items[0]],
            SelectionRole.FALLBACK: current_items[1:3],
            SelectionRole.SCIM_BASELINE: [current_items[3]],
        },
    )

    remap = build_selection_chunk_remap(previous, current)

    assert set(remap) == {item["chunkId"] for item in (primary, duplicate_a, duplicate_b, baseline)}
    assert remap[primary["chunkId"]] == {"docId": current_items[0]["docId"], "chunkId": current_items[0]["chunkId"]}
    assert remap[baseline["chunkId"]]["chunkId"] == current_items[3]["chunkId"]
    assert {remap[duplicate_a["chunkId"]]["chunkId"], remap[duplicate_b["chunkId"]]["chunkId"]} == {
        current_items[1]["chunkId"],
        current_items[2]["chunkId"],
    }


def test_remap_does_not_pair_a_chunk_playing_a_different_role():
    primary = _item("User schema")
    previous = DocumentationSelection.from_corpus([primary], {SelectionRole.PRIMARY: [primary]})
    copied = _copy_with_new_ids(primary)
    current = DocumentationSelection.from_corpus(
        [_item("Other"), copied], {SelectionRole.PRIMARY: [], SelectionRole.FALLBACK: [copied]}
    )

    assert build_selection_chunk_remap(previous, current) == {}


def test_order_sensitive_roles_are_exactly_the_scim_baseline_and_sql_schema():
    assert ORDER_SENSITIVE_SELECTION_ROLES == {SelectionRole.SCIM_BASELINE.value, SelectionRole.SQL_SCHEMA.value}
    assert ORDER_SENSITIVE_SELECTION_ROLES <= {role.value for role in SelectionRole}


@pytest.mark.parametrize("role", [SelectionRole.SCIM_BASELINE, SelectionRole.SQL_SCHEMA])
def test_reordering_an_order_sensitive_role_changes_the_cache_identity(role):
    first, second = _item("Definition one"), _item("Definition two")
    forward = DocumentationSelection.from_corpus([first, second], {role: [first, second]})
    copied_first, copied_second = _copy_with_new_ids(first), _copy_with_new_ids(second)
    reverse = DocumentationSelection.from_corpus([copied_second, copied_first], {role: [copied_second, copied_first]})
    same_order_copy = DocumentationSelection.from_corpus(
        [copied_first, copied_second], {role: [copied_first, copied_second]}
    )

    assert normalized_input_fingerprint(_input(forward)) != normalized_input_fingerprint(_input(reverse))
    assert normalized_input_fingerprint(_input(forward)) == normalized_input_fingerprint(_input(same_order_copy))


def test_chunks_are_read_in_reference_order():
    first, second = _item("Definition one"), _item("Definition two")
    stored = DocumentationSelection.from_corpus(
        [first, second], {SelectionRole.SCIM_BASELINE: [first, second]}
    ).to_job_input()
    stored["scimBaseline"] = list(reversed(stored["scimBaseline"]))

    selection = DocumentationSelection.model_validate(stored)

    assert [item["chunkId"] for item in selection.documentation_items(SelectionRole.SCIM_BASELINE)] == [
        second["chunkId"],
        first["chunkId"],
    ]


def test_remap_pairs_identical_order_sensitive_chunks_by_position():
    duplicate = _item("Repeated schema export", url="https://docs.example.com/schema")
    later_duplicate = _copy_with_new_ids(duplicate)
    previous = DocumentationSelection.from_corpus(
        [duplicate, later_duplicate], {SelectionRole.SCIM_BASELINE: [duplicate, later_duplicate]}
    )
    current_items = [_copy_with_new_ids(duplicate), _copy_with_new_ids(duplicate)]
    current = DocumentationSelection.from_corpus(current_items, {SelectionRole.SCIM_BASELINE: current_items})

    remap = build_selection_chunk_remap(previous, current)

    assert remap[duplicate["chunkId"]]["chunkId"] == current_items[0]["chunkId"]
    assert remap[later_duplicate["chunkId"]]["chunkId"] == current_items[1]["chunkId"]


def test_selected_chunks_keep_exactly_the_metadata_extraction_reads():
    """Changing this set changes what extractors see and the cache identity: bump the selection version."""
    assert SELECTED_CHUNK_METADATA_KEYS == ("content_type", "tags")
    item = _item("User schema", content_type="application/yaml", filename="spec.yaml", num_endpoints=3)

    metadata = DocumentationSelection.from_corpus([item], {SelectionRole.PRIMARY: [item]}).documentation_items(
        SelectionRole.PRIMARY
    )[0]["@metadata"]

    assert metadata == {"content_type": "application/yaml", "tags": ["user"]}
