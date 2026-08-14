# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for projecting the stored relation analysis onto relation codegen context."""

from typing import Any, Dict, List, Optional

from src.modules.codegen.selection.relation_analysis import (
    relation_documentation_classes,
    select_relation_codegen_context,
)
from src.modules.digester.entities.relation_candidates import (
    ObjectClassIndex,
    expand_link_object_pairs,
    group_observations,
    observations_from_attributes,
    verdict_to_relation_record,
)
from src.modules.digester.schemas import (
    RelationDecision,
    RelationPairAnalysis,
    RelationRecord,
    RelationsAnalysis,
    RelationVerdict,
)

_TEST_OUTPUT_FINGERPRINT = "f" * 64


def _select_context(analysis: Any, record: RelationRecord):
    if isinstance(analysis, RelationsAnalysis):
        analysis = analysis.model_copy(update={"output_fingerprint": _TEST_OUTPUT_FINGERPRINT})
    elif isinstance(analysis, dict):
        analysis = dict(analysis)
        analysis["outputFingerprint"] = _TEST_OUTPUT_FINGERPRINT
    return select_relation_codegen_context(
        analysis,
        record,
        output_fingerprint=_TEST_OUTPUT_FINGERPRINT,
    )


def _record(
    *,
    name: str = "user_to_group",
    subject: str = "user",
    object_class: str = "group",
    subject_attribute: str = "",
    object_attribute: str = "",
) -> RelationRecord:
    return RelationRecord(
        name=name,
        displayName="User to Group",
        shortDescription="",
        subject=subject,
        subjectAttribute=subject_attribute,
        object=object_class,
        objectAttribute=object_attribute,
    )


def _verdict(
    *,
    kind: str = "link_object",
    subject: str = "User",
    object_class: str = "Group",
    subject_attribute: str = "",
    object_attribute: str = "",
    link_object_class: str = "Membership",
    name: str = "user_to_group",
) -> Dict[str, Any]:
    return {
        "isRelation": True,
        "kind": kind,
        "subject": subject,
        "object": object_class,
        "subjectAttribute": subject_attribute,
        "objectAttribute": object_attribute,
        "linkObjectClass": link_object_class,
        "name": name,
    }


def _pair(
    *,
    pair_key: str,
    class_a: str,
    class_b: str,
    observations: Optional[List[Dict[str, Any]]] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "pairKey": pair_key,
        "classA": class_a,
        "classB": class_b,
        "observations": observations or [],
        "decisions": decisions or [],
    }


def _link_object_analysis(**verdict_overrides: Any) -> Dict[str, Any]:
    """Analysis of User<->Group carried by Membership, with both carrier ends observed."""
    return {
        "pairs": [
            _pair(
                pair_key="group|user",
                class_a="Group",
                class_b="User",
                observations=[
                    {
                        "sourceClass": "User",
                        "targetClass": "Group",
                        "evidenceKind": "schema_reference",
                        "viaClass": "Membership",
                    }
                ],
                decisions=[{"accepted": True, "verdict": _verdict(**verdict_overrides)}],
            ),
            _pair(
                pair_key="membership|user",
                class_a="Membership",
                class_b="User",
                observations=[
                    {
                        "sourceClass": "Membership",
                        "targetClass": "User",
                        "sourceAttribute": "userId",
                        "evidenceKind": "attribute_metadata",
                    }
                ],
            ),
            _pair(
                pair_key="group|membership",
                class_a="Group",
                class_b="Membership",
                observations=[
                    {
                        "sourceClass": "Membership",
                        "targetClass": "Group",
                        "sourceAttribute": "groupId",
                        "evidenceKind": "attribute_metadata",
                    }
                ],
            ),
        ]
    }


def test_link_object_context_carries_the_class_and_its_attributes():
    context = _select_context(_link_object_analysis(), _record())

    assert context is not None
    assert context.kind == "link_object"
    assert context.link_object_class == "Membership"
    assert [(item.attribute, item.references) for item in context.link_attributes] == [
        ("userId", "user"),
        ("groupId", "group"),
    ]
    assert context.prompt_payload() == {
        "kind": "link_object",
        "linkObjectClass": "Membership",
        "linkAttributes": [
            {"attribute": "userId", "references": "user"},
            {"attribute": "groupId", "references": "group"},
        ],
    }


def test_link_object_class_documentation_is_selected_alongside_both_ends():
    context = _select_context(_link_object_analysis(), _record())

    assert relation_documentation_classes(_record(), context) == ["user", "group", "Membership"]


def test_documentation_classes_without_context_stay_at_the_two_ends():
    assert relation_documentation_classes(_record(), None) == ["user", "group"]


def test_direct_reference_context_names_no_carrying_class():
    analysis = _link_object_analysis(kind="reference", subject_attribute="groups", link_object_class="")
    record = _record(subject_attribute="groups")

    context = _select_context(analysis, record)

    assert context is not None
    assert context.kind == "reference"
    assert context.link_object_class == ""
    assert context.link_attributes == []
    assert context.prompt_payload() == {"kind": "reference"}


def test_carrying_class_is_recovered_from_the_evidence_when_the_verdict_omits_it():
    """Adjudication can classify link_object without naming the class the pair was built from."""
    context = _select_context(_link_object_analysis(link_object_class=""), _record())

    assert context is not None
    assert context.link_object_class == "Membership"


def test_via_class_is_ignored_for_a_kind_that_is_not_link_object():
    """Adjudication decided the association is direct; the observed carrier does not override it."""
    analysis = _link_object_analysis(kind="reference", link_object_class="")

    context = _select_context(analysis, _record())

    assert context is not None
    assert context.link_object_class == ""


def test_an_end_named_as_its_own_carrier_is_rejected():
    """A carrier is a third class; the generator must not be told to resolve a class through itself."""
    analysis = _link_object_analysis(link_object_class="Group")

    context = _select_context(analysis, _record())

    assert context is not None
    assert context.link_object_class == ""
    assert context.link_attributes == []


def test_missing_analysis_yields_no_context():
    assert _select_context(None, _record()) is None


def test_unreadable_analysis_yields_no_context():
    assert _select_context({"pairs": "not-a-list"}, _record()) is None


def test_analysis_for_another_relation_output_is_not_used():
    analysis = _link_object_analysis()
    analysis["outputFingerprint"] = "a" * 64

    assert (
        select_relation_codegen_context(
            analysis,
            _record(),
            output_fingerprint="b" * 64,
        )
        is None
    )


def test_relation_absent_from_the_analysis_yields_no_context():
    analysis = _link_object_analysis(subject="Principal", object_class="Role")

    assert _select_context(analysis, _record()) is None


def test_rejected_decision_is_not_used():
    analysis = _link_object_analysis()
    analysis["pairs"][0]["decisions"][0]["accepted"] = False

    assert _select_context(analysis, _record()) is None


def test_attribute_spelling_differences_still_match_the_verdict():
    """Duplicate merging can swap one raw spelling of an attribute for a canonically equal one."""
    analysis = _link_object_analysis(kind="reference", subject_attribute="group_ids", link_object_class="")

    context = _select_context(analysis, _record(subject_attribute="groupIds"))

    assert context is not None
    assert context.kind == "reference"


def test_two_associations_of_one_pair_without_attributes_are_told_apart_by_name():
    analysis = _link_object_analysis()
    analysis["pairs"][0]["decisions"].append(
        {
            "accepted": True,
            "verdict": _verdict(kind="reference", link_object_class="", name="user_owns_group"),
        }
    )

    owned = _select_context(analysis, _record(name="user_owns_group"))
    carried = _select_context(analysis, _record(name="user_to_group"))

    assert owned is not None and owned.kind == "reference"
    assert carried is not None and carried.kind == "link_object"


def test_an_unresolvable_tie_yields_no_context():
    """Attaching one association's carrier to another one is worse than sending no context."""
    analysis = _link_object_analysis()
    analysis["pairs"][0]["decisions"].append(
        {"accepted": True, "verdict": _verdict(kind="reference", link_object_class="", name="something_else")}
    )

    assert _select_context(analysis, _record(name="user_to_group_v2")) is None


def test_schema_evidence_outranks_prose_for_the_carrier_attributes():
    analysis = _link_object_analysis()
    analysis["pairs"][1]["observations"].insert(
        0,
        {
            "sourceClass": "Membership",
            "targetClass": "User",
            "sourceAttribute": "memberName",
            "evidenceKind": "narrative",
        },
    )

    context = _select_context(analysis, _record())

    assert context is not None
    assert [item.attribute for item in context.link_attributes if item.references == "user"] == [
        "userId",
        "memberName",
    ]


def test_inheritance_evidence_never_becomes_a_carrier_attribute():
    analysis = _link_object_analysis()
    analysis["pairs"][1]["observations"] = [
        {
            "sourceClass": "Membership",
            "targetClass": "User",
            "sourceAttribute": "parent",
            "evidenceKind": "inheritance_metadata",
        }
    ]

    context = _select_context(analysis, _record())

    assert context is not None
    assert [item.references for item in context.link_attributes] == ["group"]


def test_context_survives_a_round_trip_through_the_real_pipeline_code():
    """
    Guard against fixture drift: build the analysis with the digester's own functions.

    A hand-written fixture can pass while the producer emits something else. This drives the
    real seeding, the real association-class expansion and the real projection, then joins the
    emitted record back - which is the whole point of the context.
    """
    index, skipped = ObjectClassIndex.from_payload(
        {
            "objectClasses": [
                {"name": "User", "description": "People", "confidence": "high"},
                {"name": "Group", "description": "Groups", "confidence": "high"},
                {"name": "Membership", "description": "Joins a user to a group", "confidence": "medium"},
            ]
        }
    )
    assert skipped == 0

    membership_attributes = {
        "attributes": {
            "userId": {"name": "userId", "type": "User", "format": "reference"},
            "groupId": {"name": "groupId", "type": "Group", "format": "reference"},
            "role": {"name": "role", "type": "string"},
        }
    }
    entries: List[Any] = [
        (observation, "attribute_schema", None)
        for observation in observations_from_attributes("Membership", membership_attributes, index)
    ]
    link_entries, _summary = expand_link_object_pairs(entries, index, {"membership": membership_attributes})
    assert link_entries, "the association class must produce the User<->Group pair"
    entries.extend(link_entries)

    pairs, _unresolved = group_observations(entries, index)
    verdict = RelationVerdict(
        isRelation=True,
        kind="link_object",
        subject="User",
        object="Group",
        linkObjectClass="Membership",
        name="user_to_group",
        displayName="User to Group",
    )
    analysis = RelationsAnalysis(
        pairs=[
            RelationPairAnalysis(
                pairKey=pair.key,
                classA=pair.class_a,
                classB=pair.class_b,
                observations=list(pair.observations),
                accepted=key == "group|user",
                decisions=[RelationDecision(verdict=verdict, accepted=True)] if key == "group|user" else [],
            )
            for key, pair in pairs.items()
        ]
    )

    record = verdict_to_relation_record(verdict)
    assert record is not None
    # The record midPoint receives: no attribute on either side, no trace of Membership.
    assert (record.subject_attribute, record.object_attribute) == ("", "")

    context = _select_context(analysis.model_dump(by_alias=True, mode="json"), record)

    assert context is not None
    assert context.link_object_class == "Membership"
    assert {(item.attribute, item.references) for item in context.link_attributes} == {
        ("userId", "user"),
        ("groupId", "group"),
    }
    assert relation_documentation_classes(record, context) == ["user", "group", "Membership"]


def test_all_carrier_attributes_pointing_at_an_end_are_kept():
    analysis = _link_object_analysis()
    analysis["pairs"][1]["observations"] = [
        {
            "sourceClass": "Membership",
            "targetClass": "User",
            "sourceAttribute": f"userRef{index}",
            "evidenceKind": "attribute_metadata",
        }
        for index in range(4)
    ]

    context = _select_context(analysis, _record())

    assert context is not None
    assert [item.attribute for item in context.link_attributes if item.references == "user"] == [
        "userRef0",
        "userRef1",
        "userRef2",
        "userRef3",
    ]
