# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Tests for the staged relation extraction pipeline, with the LLM passes stubbed out."""

from contextlib import ExitStack
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.modules.digester.entities.relation_candidates import ObjectClassIndex
from src.modules.digester.extractors.rest import relation_context
from src.modules.digester.extractors.rest.relations import extract_relations as run_relations_worker
from src.modules.digester.schemas.relation_analysis import (
    RelationDecision,
    RelationObservation,
    RelationPairAnalysis,
    RelationPairJudgement,
    RelationRefutation,
    RelationsAnalysis,
    RelationVerdict,
)

MODULE = "src.modules.digester.extractors.rest.relations"
CONTEXT = "src.modules.digester.extractors.rest.relation_context"

USER_CHUNK = uuid4()
GROUP_CHUNK = uuid4()
DOC_ID = uuid4()

OBJECT_CLASSES = {
    "objectClasses": [
        {"name": "User", "description": "An account", "confidence": "high"},
        {"name": "Group", "description": "An entitlement container", "confidence": "high"},
    ]
}

DOC_ITEMS = [
    {
        "docId": str(DOC_ID),
        "chunkId": str(USER_CHUNK),
        "content": "User schema: groups is an array of Group references.",
        "summary": "User schema",
        "@metadata": {"tags": ["user"]},
    },
    {
        "docId": str(DOC_ID),
        "chunkId": str(GROUP_CHUNK),
        "content": "Group schema: members lists the users in the group.",
        "summary": "Group schema",
        "@metadata": {"tags": ["group"]},
    },
]

UNRELATED_DOC_ITEMS = [
    {
        "docId": str(DOC_ID),
        "chunkId": str(USER_CHUNK),
        "content": "Rate limiting: the API allows 100 requests per minute.",
        "summary": "Rate limits",
        "@metadata": {"tags": ["limits"]},
    },
]

EMPTY_CLASS_SCHEMA_SNAPSHOT: Dict[str, Dict[str, Any]] = {
    "attributesByClass": {},
    "endpointsByClass": {},
}


async def extract_relations(
    doc_items: List[dict],
    relevant_object_classes: Any,
    session_id: UUID,
    job_id: UUID,
    *,
    class_schema_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run the worker with an explicit durable schema snapshot."""
    return await run_relations_worker(
        doc_items,
        relevant_object_classes,
        class_schema_snapshot or EMPTY_CLASS_SCHEMA_SNAPSHOT,
        session_id,
        job_id,
    )


class _FakeSessionMaker:
    """Stand-in for ``async_session_maker`` so the worker's own DB reads can be stubbed."""

    def __call__(self) -> "_FakeSessionMaker":
        return self

    async def __aenter__(self) -> MagicMock:
        return MagicMock()

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _repo_without_mapped_chunks() -> MagicMock:
    """Relevant-chunk repository for a session where no documentation maps to any class."""
    repo = MagicMock()
    repo.get_relevant_chunks_grouped_by_entity = AsyncMock(return_value={})
    return repo


def _observation(**overrides: Any) -> RelationObservation:
    payload: Dict[str, Any] = {
        "sourceClass": "User",
        "targetClass": "Group",
        "sourceAttribute": "",
        "targetAttribute": "",
        "evidenceKind": "schema_property",
        "quote": "groups: array of Group",
    }
    payload.update(overrides)
    return RelationObservation.model_validate(payload)


def _verdict(**overrides: Any) -> RelationVerdict:
    payload: Dict[str, Any] = {
        "isRelation": True,
        "kind": "reference",
        "subject": "User",
        "subjectAttribute": "groups",
        "object": "Group",
        "objectAttribute": "members",
        "name": "user_to_group",
        "displayName": "User to Group",
        "shortDescription": "Users belong to groups.",
        "confidence": "high",
    }
    payload.update(overrides)
    return RelationVerdict.model_validate(payload)


def _judgement(*verdicts: RelationVerdict, rejection_kind: str = "not_a_relation", rationale: str = "") -> Any:
    return RelationPairJudgement(
        relations=list(verdicts) or [],
        rejection_kind=rejection_kind,
        rationale=rationale,
    )


def test_nested_refutation_serializes_corrections_in_camel_case():
    """The stored analysis uses one consistent camelCase contract at every nesting level."""
    analysis = RelationsAnalysis(
        pairs=[
            RelationPairAnalysis(
                pair_key="group|user",
                class_a="Group",
                class_b="User",
                decisions=[
                    RelationDecision(
                        verdict=_verdict(),
                        refutation=RelationRefutation(
                            refuted=False,
                            corrected_subject_attribute="memberOf",
                            corrected_object_attribute="members",
                        ),
                    )
                ],
            )
        ]
    )

    refutation = analysis.model_dump(by_alias=True, mode="json")["pairs"][0]["decisions"][0]["refutation"]

    assert refutation["correctedSubjectAttribute"] == "memberOf"
    assert refutation["correctedObjectAttribute"] == "members"
    assert "corrected_subject_attribute" not in refutation
    assert "corrected_object_attribute" not in refutation


def test_unknown_pair_rejection_kind_falls_back_to_not_a_relation():
    """An unknown LLM rejection label must not be converted into an accepted relation kind."""
    judgement = RelationPairJudgement.model_validate({"relations": [], "rejection_kind": "unsupported_relation_kind"})

    assert judgement.rejection_kind == "not_a_relation"


def _harvest_results(per_chunk: Dict[UUID, List[RelationObservation]]):
    async def run_chunks_concurrently(*, chunk_items, job_id, extractor, set_total=True):
        results = []
        for item in chunk_items:
            chunk_id = UUID(item["chunkId"])
            results.append((per_chunk.get(chunk_id, []), True, chunk_id))
        return results

    return run_chunks_concurrently


def _pipeline_patches(
    stack: ExitStack,
    *,
    harvest: Dict[UUID, List[RelationObservation]],
    judgement: Optional[RelationPairJudgement],
    refutation: Optional[RelationRefutation] = None,
    attributes: Optional[Dict[str, Any]] = None,
    sweep: Optional[List[RelationObservation]] = None,
) -> Dict[str, Any]:
    """Patch every boundary of the pipeline: the DB reads and each LLM pass."""
    attributes = attributes or {}
    object_class_index, _ = ObjectClassIndex.from_payload(OBJECT_CLASSES)
    class_schema_snapshot = relation_context.build_relation_schema_snapshot(object_class_index, attributes)

    relevant_repo = MagicMock()
    relevant_repo.get_relevant_chunks_grouped_by_entity = AsyncMock(
        return_value={
            "user": [{"docId": str(DOC_ID), "chunkId": str(USER_CHUNK)}],
            "group": [{"docId": str(DOC_ID), "chunkId": str(GROUP_CHUNK)}],
        }
    )

    mocks: Dict[str, Any] = {
        "adjudicate": AsyncMock(return_value=judgement),
        "verify": AsyncMock(return_value=refutation),
        "sweep": AsyncMock(return_value=sweep or []),
        "focus": AsyncMock(return_value=[]),
        "sweep_chain": MagicMock(),
        "focus_chain": MagicMock(),
        "adjudication_chain": MagicMock(),
        "verification_chain": MagicMock(),
        "store": AsyncMock(return_value=True),
        "progress": AsyncMock(),
        "increment": AsyncMock(),
        "error": AsyncMock(),
        "class_schema_snapshot": class_schema_snapshot,
    }

    stack.enter_context(patch(f"{CONTEXT}.async_session_maker", _FakeSessionMaker()))
    stack.enter_context(patch(f"{CONTEXT}.RelevantChunkRepository", return_value=relevant_repo))
    stack.enter_context(patch(f"{MODULE}.run_chunks_concurrently", _harvest_results(harvest)))
    stack.enter_context(patch(f"{MODULE}.relation_passes.build_harvest_chain", return_value=MagicMock()))
    mocks["build_sweep_chain"] = stack.enter_context(
        patch(f"{MODULE}.relation_passes.build_class_sweep_chain", return_value=mocks["sweep_chain"])
    )
    mocks["build_focus_chain"] = stack.enter_context(
        patch(f"{MODULE}.relation_passes.build_pair_focus_chain", return_value=mocks["focus_chain"])
    )
    mocks["build_adjudication_chain"] = stack.enter_context(
        patch(f"{MODULE}.relation_passes.build_adjudication_chain", return_value=mocks["adjudication_chain"])
    )
    mocks["build_verification_chain"] = stack.enter_context(
        patch(f"{MODULE}.relation_passes.build_verification_chain", return_value=mocks["verification_chain"])
    )
    stack.enter_context(patch(f"{MODULE}.relation_passes.sweep_class", mocks["sweep"]))
    stack.enter_context(patch(f"{MODULE}.relation_passes.focus_pair", mocks["focus"]))
    stack.enter_context(patch(f"{MODULE}.relation_passes.adjudicate_pair", mocks["adjudicate"]))
    stack.enter_context(patch(f"{MODULE}.relation_passes.verify_relation", mocks["verify"]))
    stack.enter_context(patch(f"{MODULE}.store_relations_analysis", mocks["store"]))
    stack.enter_context(patch(f"{MODULE}.update_job_progress", mocks["progress"]))
    stack.enter_context(patch(f"{MODULE}.increment_processed_documents", mocks["increment"]))
    stack.enter_context(patch(f"{MODULE}.append_job_error", mocks["error"]))
    return mocks


# ==================== PROMPT TEMPLATES ====================


def test_every_relation_prompt_renders_through_langchain():
    """
    An unescaped brace in a prompt only fails when the chain is built, at runtime.

    The prompts are full of literal Groovy and JSON, so this guards the one edit that would
    take the whole endpoint down without any test noticing.
    """
    from langchain_core.prompts import ChatPromptTemplate

    from src.modules.digester.prompts.rest import relations_prompts as prompts

    stages = [
        (
            prompts.get_relation_harvest_system_prompt,
            prompts.get_relation_harvest_user_prompt,
            {"object_classes": "[]", "summary": "s", "tags": "t", "chunk": "c"},
        ),
        (
            prompts.get_relation_class_sweep_system_prompt,
            prompts.get_relation_class_sweep_user_prompt,
            {"focus_class": "User", "focus_description": "d", "object_classes": "[]", "documentation": "doc"},
        ),
        (
            prompts.get_relation_pair_focus_system_prompt,
            prompts.get_relation_pair_focus_user_prompt,
            {
                "class_a": "OpaqueA",
                "class_b": "OpaqueB",
                "class_metadata": "[]",
                "known_observations": "[]",
                "documentation": "doc",
            },
        ),
        (
            prompts.get_relation_adjudication_system_prompt,
            prompts.get_relation_adjudication_user_prompt,
            {
                "class_a": "User",
                "class_b": "Group",
                "class_metadata": "[]",
                "known_attributes": "{}",
                "observed_attributes": "{}",
                "observations": "[]",
            },
        ),
        (
            prompts.get_relation_verification_system_prompt,
            prompts.get_relation_verification_user_prompt,
            {"relation": "{}", "class_metadata": "[]", "observations": "[]", "known_attributes": "{}"},
        ),
    ]

    for system_prompt, user_prompt, variables in stages:
        template = ChatPromptTemplate.from_messages(
            [("system", f"{system_prompt}\n\n{{format_instructions}}"), ("human", user_prompt)]
        ).partial(format_instructions="FI")
        messages = template.format_messages(**variables)
        assert len(messages) == 2
        assert "relation_ontology" in messages[0].content


# ==================== PIPELINE ====================


@pytest.mark.asyncio
async def test_opposite_sides_in_two_chunks_yield_one_relation():
    """
    The behaviour the staged pipeline exists for.

    The User schema and the Group schema live in different chunks, so each chunk can only
    see one side. Both sides must reach a single adjudication call and a single relation.
    """
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={
                USER_CHUNK: [_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups")],
                GROUP_CHUNK: [_observation(sourceClass="Group", targetClass="User", sourceAttribute="members")],
            },
            judgement=_judgement(_verdict()),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    relations = result["result"]["relations"]
    assert len(relations) == 1
    assert relations[0]["subject"] == "user"
    assert relations[0]["subjectAttribute"] == "groups"
    assert relations[0]["object"] == "group"
    assert relations[0]["objectAttribute"] == "members"

    mocks["adjudicate"].assert_awaited_once()
    observations = mocks["adjudicate"].await_args.kwargs["observations"]
    assert len(observations) == 2, "both halves must reach the same adjudication call"
    assert mocks["sweep"].await_count == 2
    mocks["build_sweep_chain"].assert_called_once_with()
    assert all(call.kwargs["chain"] is mocks["sweep_chain"] for call in mocks["sweep"].await_args_list)


@pytest.mark.asyncio
async def test_api_payload_keeps_the_relations_response_contract():
    """Everything the pipeline learns beyond the seven contract fields stays out of the response."""
    with ExitStack() as stack:
        _pipeline_patches(stack, harvest={USER_CHUNK: [_observation()]}, judgement=_judgement(_verdict()))
        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert set(result) == {"result", "relevantDocumentations"}
    assert set(result["result"]) == {"relations"}
    assert set(result["result"]["relations"][0]) == {
        "name",
        "displayName",
        "shortDescription",
        "subject",
        "subjectAttribute",
        "object",
        "objectAttribute",
    }


@pytest.mark.asyncio
async def test_analysis_is_persisted_with_the_rejected_pairs():
    """Rejections are recorded so a reviewer confirms them once instead of on every run."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation()]},
            judgement=_judgement(rejection_kind="embedded", rationale="Group is embedded in User."),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert result["result"]["relations"] == []
    mocks["store"].assert_awaited_once()
    stored = mocks["store"].await_args.args[2]
    assert stored["stats"]["pairsAdjudicated"] == 1
    assert stored["pairs"][0]["accepted"] is False
    assert stored["pairs"][0]["decisions"] == []
    assert "embedded" in stored["pairs"][0]["rejectionReason"].lower()


@pytest.mark.asyncio
async def test_refuted_relation_is_dropped_with_a_recorded_reason():
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation()]},
            judgement=_judgement(_verdict()),
            refutation=RelationRefutation(refuted=True, reason="Only a nearby mention, no reference."),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert result["result"]["relations"] == []
    stored = mocks["store"].await_args.args[2]
    assert stored["pairs"][0]["decisions"][0]["rejectionReason"] == "Only a nearby mention, no reference."
    assert stored["pairs"][0]["accepted"] is False


@pytest.mark.asyncio
async def test_verification_correction_is_applied_to_the_attribute_name():
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceAttribute="memberOf")]},
            judgement=_judgement(_verdict(subjectAttribute="groups")),
            refutation=RelationRefutation(refuted=False, corrected_subject_attribute="memberOf"),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert result["result"]["relations"][0]["subjectAttribute"] == "memberOf"


@pytest.mark.asyncio
async def test_invented_attribute_name_is_cleared_but_the_relation_survives():
    """A fabricated name reaches codegen as a real one, so an empty side is strictly better."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceAttribute="groups")]},
            judgement=_judgement(_verdict(subjectAttribute="groups", objectAttribute="totallyMadeUp")),
            attributes={
                "userAttributesOutput": {"attributes": {"groups": {"type": "Group", "format": "reference"}}},
                "groupAttributesOutput": {"attributes": {"displayName": {"type": "string"}}},
            },
        )

        result = await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    relation = result["result"]["relations"][0]
    assert relation["subjectAttribute"] == "groups"
    assert relation["objectAttribute"] == ""

    stored = mocks["store"].await_args.args[2]
    assert stored["pairs"][0]["decisions"][0]["ungroundedAttributes"] == ["Group.totallyMadeUp"]


@pytest.mark.asyncio
async def test_verdict_naming_a_foreign_class_is_rejected_instead_of_guessed():
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation()]},
            judgement=_judgement(_verdict(subject="Widget", object="Sprocket")),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert result["result"]["relations"] == []


@pytest.mark.asyncio
async def test_opaque_class_roles_come_from_llm_and_prompt_metadata_has_no_relevance_ids():
    opaque_classes = {
        "objectClasses": [
            {
                "name": "XQ17",
                "description": "An independently managed resource whose instances receive scoped access.",
                "confidence": "high",
                "relevantDocumentations": [{"docId": "secret-doc", "chunkId": "secret-chunk"}],
            },
            {
                "name": "Zeta9",
                "description": "An independently managed resource whose instances define that access scope.",
                "confidence": "high",
                "relevantDocumentations": [{"docId": "other-doc", "chunkId": "other-chunk"}],
            },
        ]
    }
    opaque_docs = [
        dict(DOC_ITEMS[0], content="XQ17 instances reference Zeta9 scopes."),
        dict(DOC_ITEMS[1], content="Zeta9 defines the scope received by XQ17."),
    ]
    observation = _observation(
        sourceClass="XQ17",
        targetClass="Zeta9",
        sourceAttribute="zetaRefs",
        quote="XQ17 instances reference Zeta9 scopes.",
    )
    verdict = _verdict(
        subject="XQ17",
        subjectAttribute="zetaRefs",
        object="Zeta9",
        objectAttribute="",
        name="xq17_to_zeta9",
        displayName="XQ17 to Zeta9",
    )

    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [observation]},
            judgement=_judgement(verdict),
        )

        result = await extract_relations(opaque_docs, opaque_classes, uuid4(), uuid4())

    relation = result["result"]["relations"][0]
    assert relation["subject"] == "xq17"
    assert relation["object"] == "zeta9"

    for mock_name in ("focus", "adjudicate", "verify"):
        metadata = mocks[mock_name].await_args.kwargs["class_metadata"]
        assert {item["name"] for item in metadata} == {"XQ17", "Zeta9"}
        assert all(item["description"] for item in metadata)
        assert all("relevantDocumentations" not in item for item in metadata)
        assert "secret-doc" not in str(metadata)


@pytest.mark.asyncio
async def test_stored_attribute_schema_seeds_a_pair_without_any_harvest_evidence():
    """A reference attribute already extracted is a relation candidate on its own."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(_verdict()),
            attributes={
                "userAttributesOutput": {
                    "attributes": {"groups": {"type": "Group", "format": "reference", "multivalue": True}}
                },
            },
        )

        result = await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    assert len(result["result"]["relations"]) == 1
    stored = mocks["store"].await_args.args[2]
    assert stored["stats"]["observationsDeterministic"] == 1
    assert "attribute_schema" in stored["pairs"][0]["observationSources"]


@pytest.mark.asyncio
async def test_recursive_reference_attributes_seed_and_emit_a_self_relation():
    """A hierarchy must survive on schema evidence alone, without depending on the capped sweep.

    Uses the ``reference NAME`` spelling REST extraction actually produces, so the test fails
    if either the reference marker or the self-target is dropped before the pair is formed.
    """
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(
                _verdict(
                    subject="Group",
                    subjectAttribute="parentGroup",
                    object="Group",
                    objectAttribute="childGroups",
                    name="group_to_group",
                    displayName="Group hierarchy",
                )
            ),
            attributes={
                "groupAttributesOutput": {
                    "attributes": {
                        "parentGroup": {"type": "reference Group", "format": "reference"},
                        "childGroups": {"type": "reference Group", "format": "reference", "multivalue": True},
                    }
                },
            },
        )

        result = await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    relations = result["result"]["relations"]
    assert len(relations) == 1
    # The response contract normalizes class names, as it does for every other relation.
    assert relations[0]["subject"] == "group"
    assert relations[0]["object"] == "group"
    # Both sides stay named: grounding resolves them against the one class of a self pair.
    assert relations[0]["subjectAttribute"] == "parentGroup"
    assert relations[0]["objectAttribute"] == "childGroups"

    stored = mocks["store"].await_args.args[2]
    assert stored["stats"]["observationsDeterministic"] == 2

    # A self pair has one class on both sides, so its two attribute buckets must be merged
    # rather than collapsed onto a single key that hides one of them.
    observed = mocks["adjudicate"].await_args.kwargs["observed_attributes"]
    assert observed == {"Group": ["parentGroup", "childGroups"]}

    # The judged class is described once, not twice, even though it is both sides of the pair.
    class_metadata = mocks["adjudicate"].await_args.kwargs["class_metadata"]
    assert [item["name"] for item in class_metadata] == ["Group"]


@pytest.mark.asyncio
async def test_every_step_announces_itself_and_resets_its_counters():
    """The CLI must be able to say which step is running, over what, and how far along it is."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(_verdict()),
            attributes={
                "userAttributesOutput": {"attributes": {"groups": {"type": "Group", "format": "reference"}}},
            },
        )

        await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    steps = [call.kwargs for call in mocks["progress"].await_args_list if call.kwargs.get("message")]
    messages = [step["message"] for step in steps]

    assert [message.split(" - ")[0] for message in messages] == [
        "Step 1/6",
        "Step 2/6",
        "Step 3/6",
        "Step 4/6",
        "Step 5/6",
        "Step 6/6",
    ]
    # Each step names its own unit of work, so completed/total is never ambiguous.
    assert "object classes" in messages[0]
    assert "documentation chunks" in messages[1]
    assert "candidate pairs" in messages[4]

    # Every step rebases the counters onto its own unit instead of inheriting the previous one.
    assert all(step["total_processing"] is not None for step in steps)
    assert all(step["processing_completed"] is not None for step in steps)


@pytest.mark.asyncio
async def test_long_running_steps_report_each_finished_unit():
    """Without a per-unit increment the CLI sits at 0/N for the whole step, which is the bug."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(_verdict()),
            attributes={
                "userAttributesOutput": {"attributes": {"groups": {"type": "Group", "format": "reference"}}},
            },
        )

        await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    # Two swept classes, one weak pair re-read, one pair judged and one relation verified.
    assert mocks["increment"].await_count == 5
    assert all(call.kwargs["delta"] == 1 for call in mocks["increment"].await_args_list)


@pytest.mark.asyncio
async def test_a_unit_that_never_reaches_the_llm_is_still_counted():
    """An early exit that skips its increment would strand the step below its own total."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(_verdict()),
            attributes={
                "userAttributesOutput": {"attributes": {"groups": {"type": "Group", "format": "reference"}}},
            },
        )
        # Neither recorded relevance nor the name-scan fallback maps a chunk to a class, so the
        # focused re-read bails out before its LLM call - the pair still has to be counted.
        stack.enter_context(patch(f"{CONTEXT}.RelevantChunkRepository", return_value=_repo_without_mapped_chunks()))

        await extract_relations(
            UNRELATED_DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    focus_step = next(
        call.kwargs
        for call in mocks["progress"].await_args_list
        if str(call.kwargs.get("message", "")).startswith("Step 4/6")
    )
    assert mocks["focus"].await_count == 0
    # One unit announced, one unit counted, even though the LLM was never called for it.
    assert focus_step["total_processing"] == 1
    # Focus, judge and verify: the sweep is skipped outright when nothing maps to a class.
    assert mocks["increment"].await_count == 3


@pytest.mark.asyncio
async def test_no_usable_object_classes_reports_an_error_and_calls_no_llm():
    """A blank class list must be visible on the job, not a quiet empty success."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(stack, harvest={}, judgement=None)

        result = await extract_relations(DOC_ITEMS, {"objectClasses": [{"description": "no name"}]}, uuid4(), uuid4())

    assert result["result"]["relations"] == []
    mocks["error"].assert_awaited_once()
    mocks["adjudicate"].assert_not_awaited()
    mocks["sweep"].assert_not_awaited()


@pytest.mark.asyncio
async def test_weak_pair_triggers_a_focused_reread():
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceAttribute="groups")]},
            judgement=_judgement(_verdict()),
        )

        await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    mocks["focus"].assert_awaited_once()
    assert {mocks["focus"].await_args.kwargs["class_a"], mocks["focus"].await_args.kwargs["class_b"]} == {
        "User",
        "Group",
    }


@pytest.mark.asyncio
async def test_one_class_pair_can_carry_several_distinct_associations():
    """
    ``Project.owners`` and ``Project.members`` both point at User and are two ConnId
    reference attributes. Folding onto the class pair must not collapse them into one.
    """
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceClass="Group", targetClass="User", sourceAttribute="owners")]},
            judgement=_judgement(
                _verdict(name="user_to_group", subjectAttribute="ownedGroups", objectAttribute="owners"),
                _verdict(name="user_to_group", subjectAttribute="groups", objectAttribute="members"),
            ),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    relations = result["result"]["relations"]
    assert len(relations) == 2
    assert {relation["objectAttribute"] for relation in relations} == {"owners", "members"}
    # Colliding names are renamed, never dropped: codegen resolves a relation by name.
    assert len({relation["name"] for relation in relations}) == 2


@pytest.mark.asyncio
async def test_two_endpoint_only_associations_are_kept_apart_by_their_names():
    """
    Membership and ownership can both be endpoint-only, with no attribute named on either side.

    Nothing structural then distinguishes the two records, so the identifiers the adjudication
    stage assigned have to carry the distinction instead of being collapsed as duplicates.
    """
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation()]},
            judgement=_judgement(
                _verdict(
                    name="user_to_group_membership",
                    displayName="User to Group (membership)",
                    kind="virtual_endpoint",
                    subjectAttribute="",
                    objectAttribute="",
                ),
                _verdict(
                    name="user_to_group_ownership",
                    displayName="User to Group (ownership)",
                    kind="virtual_endpoint",
                    subjectAttribute="",
                    objectAttribute="",
                ),
            ),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    relations = result["result"]["relations"]
    assert len(relations) == 2
    assert {relation["name"] for relation in relations} == {
        "user_to_group_membership",
        "user_to_group_ownership",
    }


@pytest.mark.asyncio
async def test_verification_does_not_rewrite_one_association_into_another():
    """
    Verification judges one association but sees the whole pair's evidence.

    A correction lifted from a sibling association would turn two distinct relations into
    duplicates, so a correction is only applied where it does not collide with a sibling.
    """
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceAttribute="groups", targetAttribute="members")]},
            judgement=_judgement(
                _verdict(name="user_to_group_membership", subjectAttribute="groups", objectAttribute="members"),
                _verdict(name="user_to_group_ownership", subjectAttribute="ownedGroups", objectAttribute="owners"),
            ),
            refutation=RelationRefutation(
                refuted=False,
                reason="the evidence names groups/members",
                corrected_subject_attribute="groups",
                corrected_object_attribute="members",
            ),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    relations = result["result"]["relations"]
    assert len(relations) == 2, "a sibling correction must not collapse the two associations"
    assert {relation["objectAttribute"] for relation in relations} == {"members", "owners"}


@pytest.mark.asyncio
async def test_attribute_observed_on_the_other_class_does_not_ground_this_side():
    """`members` is evidence for Group; it must not pass as a User attribute."""
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={},
            judgement=_judgement(_verdict(subjectAttribute="members", objectAttribute="members")),
            attributes={
                "userAttributesOutput": {"attributes": {"id": {"type": "string"}, "groups": {"type": "Group"}}},
                "groupAttributesOutput": {"attributes": {"id": {"type": "string"}, "members": {"type": "User"}}},
            },
            sweep=[_observation(sourceClass="Group", targetClass="User", sourceAttribute="members")],
        )

        result = await extract_relations(
            DOC_ITEMS,
            OBJECT_CLASSES,
            uuid4(),
            uuid4(),
            class_schema_snapshot=mocks["class_schema_snapshot"],
        )

    relation = result["result"]["relations"][0]
    assert relation["objectAttribute"] == "members", "Group.members is grounded"
    assert relation["subjectAttribute"] == "", "User.members is not, and must be cleared"

    stored = mocks["store"].await_args.args[2]
    assert stored["pairs"][0]["decisions"][0]["ungroundedAttributes"] == ["User.members"]


@pytest.mark.asyncio
async def test_two_associations_are_distinguishable_to_a_human():
    """Identifiers differing is not enough; a reviewer reads the display name."""
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation()]},
            judgement=_judgement(
                _verdict(name="a", displayName="User to Group", subjectAttribute="", objectAttribute="members"),
                _verdict(name="b", displayName="User to Group", subjectAttribute="", objectAttribute="owners"),
            ),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    labels = {relation["displayName"] for relation in result["result"]["relations"]}
    assert len(labels) == 2, f"both associations still read the same: {labels}"


@pytest.mark.asyncio
async def test_an_association_merged_away_is_recorded_as_such():
    """
    The persisted analysis must not claim an association that midPoint never received.

    Two verdicts that end up structurally identical are collapsed by semantic deduplication;
    the decision that lost has to say so rather than stay marked accepted.
    """
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={USER_CHUNK: [_observation(sourceAttribute="groups", targetAttribute="members")]},
            judgement=_judgement(
                _verdict(name="first", subjectAttribute="groups", objectAttribute="members"),
                _verdict(name="second", subjectAttribute="groups", objectAttribute="members"),
            ),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    assert len(result["result"]["relations"]) == 1
    stored = mocks["store"].await_args.args[2]
    decisions = stored["pairs"][0]["decisions"]
    assert [decision["accepted"] for decision in decisions] == [True, False]
    assert "Merged into" in decisions[1]["rejectionReason"]
    assert stored["stats"]["relationsEmitted"] == 1


@pytest.mark.asyncio
async def test_relevant_documentations_point_at_the_accepted_relation_evidence():
    with ExitStack() as stack:
        _pipeline_patches(
            stack,
            harvest={
                USER_CHUNK: [_observation(sourceAttribute="groups")],
                GROUP_CHUNK: [_observation(sourceClass="Group", targetClass="User", sourceAttribute="members")],
            },
            judgement=_judgement(_verdict()),
        )

        result = await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    refs = result["relevantDocumentations"]
    assert {ref["chunk_id"] for ref in refs} == {str(USER_CHUNK), str(GROUP_CHUNK)}
    assert all(ref["doc_id"] == str(DOC_ID) for ref in refs)


def test_class_schema_snapshot_captures_all_relation_dependencies():
    object_class_index, _ = ObjectClassIndex.from_payload(OBJECT_CLASSES)
    requested_keys = set(relation_context.relation_schema_output_keys(object_class_index))
    stored_values = {
        "userAttributesOutput": {"attributes": {"groups": {"type": "Group"}}},
        "userEndpointsOutput": {"endpoints": {"groups": {"path": "/Users/{id}/Groups"}}},
    }

    snapshot = relation_context.build_relation_schema_snapshot(object_class_index, stored_values)

    assert requested_keys == {
        "userAttributesOutput",
        "userEndpointsOutput",
        "groupAttributesOutput",
        "groupEndpointsOutput",
    }
    assert snapshot == {
        "attributesByClass": {"user": stored_values["userAttributesOutput"]},
        "endpointsByClass": {"user": stored_values["userEndpointsOutput"]},
    }


@pytest.mark.asyncio
async def test_persisted_observations_respect_the_storage_limit():
    with ExitStack() as stack:
        mocks = _pipeline_patches(
            stack,
            harvest={
                USER_CHUNK: [_observation(sourceAttribute="groups")],
                GROUP_CHUNK: [_observation(sourceClass="Group", targetClass="User", sourceAttribute="members")],
            },
            judgement=_judgement(_verdict()),
        )
        stack.enter_context(patch(f"{MODULE}.config.digester.relation_max_stored_observations_per_pair", 1))

        await extract_relations(DOC_ITEMS, OBJECT_CLASSES, uuid4(), uuid4())

    stored = mocks["store"].await_args.args[2]
    assert stored["stats"]["observationsTotal"] == 2
    assert len(stored["pairs"][0]["observations"]) == 1
    assert len(mocks["adjudicate"].await_args.kwargs["observations"]) == 2
