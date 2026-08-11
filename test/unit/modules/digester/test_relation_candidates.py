# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Pure-logic tests for relation candidate derivation, pairing and projection."""

from typing import Any, Dict, List

from src.modules.digester.entities.relation_candidates import (
    ObjectClassIndex,
    deduplicate_relation_names,
    default_relation_name,
    grounded_attribute_names,
    group_observations,
    is_attribute_grounded,
    observations_from_attributes,
    observations_from_class_metadata,
    observations_from_endpoints,
    pair_key,
    select_attributes_map,
    sort_relations_by_iga_priority,
    verdict_to_relation_record,
)
from src.modules.digester.schemas import RelationRecord
from src.modules.digester.schemas.relation_analysis import RelationObservation, RelationVerdict


def _object_classes(*entries: Dict[str, Any]) -> Dict[str, Any]:
    return {"objectClasses": list(entries)}


def _index(*entries: Dict[str, Any]) -> ObjectClassIndex:
    index, _skipped = ObjectClassIndex.from_payload(_object_classes(*entries))
    return index


def _user_group_index() -> ObjectClassIndex:
    return _index(
        {"name": "User", "description": "An account", "confidence": "high"},
        {"name": "Group", "description": "An entitlement container", "confidence": "high"},
    )


def _observation(**overrides: Any) -> RelationObservation:
    payload: Dict[str, Any] = {
        "sourceClass": "User",
        "targetClass": "Group",
        "sourceAttribute": "",
        "targetAttribute": "",
        "evidenceKind": "schema_property",
    }
    payload.update(overrides)
    return RelationObservation.model_validate(payload)


# ==================== OBJECT CLASS INDEX ====================


def test_index_keeps_valid_classes_when_one_entry_is_malformed():
    """One malformed entry costs that entry, never the whole class list."""
    index, skipped = ObjectClassIndex.from_payload(
        _object_classes(
            {"name": "User", "description": "u", "confidence": "high"},
            {"description": "no name at all"},
            {"name": "Group", "confidence": "not-a-level"},
        )
    )

    assert skipped == 1
    assert len(index) == 2
    assert index.resolve("User") is not None
    # An unparseable confidence degrades to the safest bucket rather than dropping the class.
    assert index.resolve("Group").confidence.value == "low"


def test_index_resolves_case_and_whitespace_without_interpreting_names():
    index = _user_group_index()

    assert index.resolve("GROUP").name == "Group"
    assert index.resolve("  group  ").name == "Group"
    assert index.resolve("users") is None
    assert index.resolve("Widget") is None
    assert index.resolve("") is None


def test_index_candidates_skip_embedded_and_abstract_classes():
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "UserName", "confidence": "high", "embedded": True},
        {"name": "BaseResource", "confidence": "high", "abstract": True},
        {"name": "Audit", "confidence": "low"},
    )

    names = [info.name for info in index.candidates(max_confidence_rank=1, limit=10)]

    assert names == ["User"]


def test_prompt_payload_carries_semantics_but_excludes_storage_references():
    """Prompt metadata is semantic context, not session persistence metadata."""
    index = _index(
        {
            "name": "Opaque17",
            "description": "Instances receive documented access from another resource.",
            "confidence": "medium",
            "embedded": True,
            "superclass": "Base",
            "relevantDocumentations": [
                {"docId": "ce57cc75-8b04-4e3b-bbd9-96b17aece86f", "chunkId": "fb18ae13-d5b0-4148-b3c9-42cd24f0fde0"}
            ],
        }
    )

    payload = index.to_prompt_payload(index.all)

    assert payload[0]["description"].startswith("Instances receive")
    assert payload[0]["embedded"] is True
    assert payload[0]["superclass"] == "Base"
    assert payload[0]["confidence"] == "medium"
    assert "relevantDocumentations" not in payload[0]
    assert "docId" not in str(payload)
    assert "chunkId" not in str(payload)


# ==================== PAIRING ====================


def test_pair_key_is_orientation_independent():
    assert pair_key("User", "Group") == pair_key("Group", "User")


def test_opposite_sides_from_different_chunks_fold_into_one_pair():
    """
    The defect this pipeline exists to fix.

    Chunking puts the User schema and the Group schema in different fragments, so one
    association arrives as two half-observations. They must end up on one pair.
    """
    index = _user_group_index()
    entries = [
        (_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups"), "chunk_harvest", None),
        (_observation(sourceClass="Group", targetClass="User", sourceAttribute="members"), "chunk_harvest", None),
    ]

    pairs, unresolved = group_observations(entries, index)

    assert unresolved == 0
    assert len(pairs) == 1
    pair = next(iter(pairs.values()))
    assert len(pair.observations) == 2
    assert pair.has_both_sides()
    assert not pair.is_weak()


def test_observations_naming_unknown_classes_are_dropped_and_counted():
    index = _user_group_index()
    entries = [
        (_observation(sourceClass="User", targetClass="Group"), "chunk_harvest", None),
        (_observation(sourceClass="User", targetClass="Sprocket"), "chunk_harvest", None),
    ]

    pairs, unresolved = group_observations(entries, index)

    assert len(pairs) == 1
    assert unresolved == 1


def test_single_sided_pair_is_weak_and_two_source_pair_is_not():
    index = _user_group_index()

    one_sided, _ = group_observations(
        [(_observation(sourceAttribute="groups"), "chunk_harvest", None)],
        index,
    )
    assert next(iter(one_sided.values())).is_weak()

    corroborated, _ = group_observations(
        [
            (_observation(sourceAttribute="groups"), "chunk_harvest", None),
            (
                _observation(sourceClass="Group", targetClass="User", sourceAttribute="members"),
                "attribute_schema",
                None,
            ),
        ],
        index,
    )
    assert not next(iter(corroborated.values())).is_weak()


def test_grouping_normalizes_class_names_to_the_extracted_spelling():
    index = _user_group_index()

    pairs, _ = group_observations(
        [(_observation(sourceClass="user", targetClass="GROUP"), "chunk_harvest", None)],
        index,
    )

    pair = next(iter(pairs.values()))
    assert {pair.class_a, pair.class_b} == {"User", "Group"}
    assert pair.observations[0].source_class == "User"


# ==================== DETERMINISTIC SEEDING ====================


def test_reference_attribute_becomes_an_observation():
    index = _user_group_index()
    payload = {
        "attributes": {
            "groups": {"type": "Group", "format": "reference", "multivalue": True, "description": "Member of"},
        }
    }

    observations = observations_from_attributes("User", payload, index)

    assert len(observations) == 1
    assert observations[0].target_class == "Group"
    assert observations[0].source_attribute == "groups"
    assert observations[0].multi_valued is True
    assert observations[0].evidence_kind == "attribute_metadata"


def test_string_false_multivalue_is_not_treated_as_true():
    index = _user_group_index()
    payload = {"attributes": {"group": {"type": "Group", "format": "reference", "multivalue": "false"}}}

    observations = observations_from_attributes("User", payload, index)

    assert observations[0].multi_valued is False


def test_embedded_attribute_is_recorded_as_a_rejection_signal():
    """An embedded complex type must be classified, not silently ignored, or it comes back next run."""
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "UserName", "confidence": "medium", "embedded": True},
    )
    payload = {"attributes": {"name": {"type": "UserName", "format": "embedded", "multivalue": False}}}

    observations = observations_from_attributes("User", payload, index)

    assert observations[0].evidence_kind == "embedded_metadata"


def test_attributes_pointing_at_their_own_class_are_ignored():
    index = _user_group_index()
    payload = {"attributes": {"self": {"type": "User", "format": "reference"}}}

    assert observations_from_attributes("User", payload, index) == []


def test_sub_resource_endpoint_becomes_an_observation():
    index = _user_group_index()
    payload = {
        "endpoints": [
            {"path": "/User/{id}/Group", "method": "GET"},
            {"path": "/User/{id}", "method": "GET"},
            {"path": "/Group/{id}/opaqueAttribute", "method": "GET"},
        ]
    }

    observations = observations_from_endpoints("User", payload, index)

    # Only the path whose both literal segments resolve to extracted classes is used;
    # The other tail is an attribute, not an exact class identifier, and is left to the LLM.
    assert len(observations) == 1
    assert observations[0].source_class == "User"
    assert observations[0].target_class == "Group"
    assert observations[0].evidence_kind == "endpoint_path"


def test_superclass_becomes_an_inheritance_observation():
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "AdminUser", "confidence": "medium", "superclass": "User"},
    )

    observations = observations_from_class_metadata(index)

    assert len(observations) == 1
    assert observations[0].evidence_kind == "inheritance_metadata"
    assert observations[0].source_class == "AdminUser"


def test_select_attributes_map_accepts_both_stored_shapes():
    assert select_attributes_map({"attributes": {"a": {"type": "string"}}}) == {"a": {"type": "string"}}
    assert select_attributes_map({"a": {"type": "string"}}) == {"a": {"type": "string"}}
    assert select_attributes_map({"attributes": "broken"}) == {}
    assert select_attributes_map(None) == {}


# ==================== PROJECTION ====================


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


def test_verdict_projects_onto_the_unchanged_contract():
    record = verdict_to_relation_record(_verdict())

    assert isinstance(record, RelationRecord)
    assert record.subject == "user"
    assert record.object == "group"
    assert record.subject_attribute == "groups"
    assert record.object_attribute == "members"
    assert set(record.model_dump(by_alias=True)) == {
        "name",
        "displayName",
        "shortDescription",
        "subject",
        "subjectAttribute",
        "object",
        "objectAttribute",
    }


def test_verdict_without_both_classes_projects_to_nothing():
    assert verdict_to_relation_record(_verdict(subject="")) is None


def test_verdict_without_a_name_gets_the_default_pattern():
    record = verdict_to_relation_record(_verdict(name="", displayName=""))

    assert record.name == "user_to_group"
    assert record.display_name == "User to Group"


def test_default_relation_name_suffixes_on_the_subject_attribute():
    assert default_relation_name("user", "group") == "user_to_group"
    assert default_relation_name("user", "group", "primaryGroup") == "user_to_group_via_primary_group"


def test_duplicate_relation_names_are_renamed_not_dropped():
    """Codegen resolves a relation by name, so a collision would make one record unreachable."""
    relations = [
        RelationRecord(
            name="user_to_group",
            display_name="User to Group",
            subject="user",
            subject_attribute="groups",
            object="group",
            object_attribute="",
        ),
        RelationRecord(
            name="user_to_group",
            display_name="User to Group",
            subject="user",
            subject_attribute="primaryGroup",
            object="group",
            object_attribute="",
        ),
    ]

    unique: List[RelationRecord] = deduplicate_relation_names(relations)

    assert len(unique) == 2
    assert len({relation.name for relation in unique}) == 2
    assert unique[1].name == "user_to_group_via_primary_group"


def test_relation_sorting_preserves_object_class_iga_priority():
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "Group", "confidence": "medium"},
        {"name": "Role", "confidence": "low"},
    )
    relations = [
        _verdict(subject="Role", object="User", subjectAttribute="owner"),
        _verdict(subject="Group", object="Role", subjectAttribute="roles"),
        _verdict(subject="User", object="Group", subjectAttribute="groups"),
    ]
    records = [verdict_to_relation_record(verdict) for verdict in relations]

    ordered = sort_relations_by_iga_priority([record for record in records if record is not None], index)

    assert [(record.subject, record.object) for record in ordered] == [
        ("user", "group"),
        ("group", "role"),
        ("role", "user"),
    ]


# ==================== GROUNDING ====================


def test_attribute_grounding_ignores_case_and_separators():
    known = grounded_attribute_names({"attributes": {"groupIds": {}, "displayName": {}}})

    assert is_attribute_grounded("group_ids", known)
    assert is_attribute_grounded("groupIds", known)
    assert not is_attribute_grounded("memberOf", known)


def test_attribute_grounding_is_skipped_when_no_attributes_were_extracted():
    """Absent attribute output means unknown, not disproven."""
    assert is_attribute_grounded("anything", set())
    assert is_attribute_grounded("", {"known"})


# ==================== LLM VOCABULARY TOLERANCE ====================


def test_out_of_vocabulary_llm_values_degrade_instead_of_failing_validation():
    """
    A raised ValidationError would discard every other observation in the same response,
    so an unrecognized label must cost only that label.
    """
    coerced = RelationObservation.model_validate(
        {"sourceClass": "User", "targetClass": "Group", "evidenceKind": "Schema-Property"}
    )
    assert coerced.evidence_kind == "schema_property"

    unknown = RelationObservation.model_validate(
        {"sourceClass": "User", "targetClass": "Group", "evidenceKind": "something new"}
    )
    assert unknown.evidence_kind == "narrative"

    verdict = RelationVerdict.model_validate(
        {"isRelation": True, "kind": "invented", "subject": "User", "object": "Group", "confidence": "very high"}
    )
    assert verdict.kind == "reference"
    assert verdict.confidence.value == "low"
