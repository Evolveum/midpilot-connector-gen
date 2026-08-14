# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Pure-logic tests for relation candidate derivation, pairing and projection."""

from typing import Any, Dict, List
from unittest.mock import patch

from src.config import config
from src.modules.digester.entities.relation_candidates import (
    ObjectClassIndex,
    deduplicate_relation_names,
    default_relation_name,
    disambiguate_relation_labels,
    expand_link_object_pairs,
    grounded_attribute_names,
    group_observations,
    inspect_attribute_schema,
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


def test_semantically_duplicate_observations_are_merged_with_richer_evidence():
    index = _user_group_index()
    entries = [
        (
            _observation(sourceAttribute="groups", quote="short", multiValued=None),
            "chunk_harvest",
            [{"doc_id": "doc-1", "chunk_id": "chunk-1"}],
        ),
        (
            _observation(
                sourceAttribute="groups",
                quote="A longer citation naming the same relation",
                multiValued=True,
            ),
            "class_sweep",
            [{"doc_id": "doc-2", "chunk_id": "chunk-2"}],
        ),
    ]

    pairs, _ = group_observations(entries, index)
    pair = next(iter(pairs.values()))

    assert len(pair.observations) == 1
    assert pair.observations[0].multi_valued is True
    assert pair.observations[0].quote == "A longer citation naming the same relation"
    assert pair.sources == ["chunk_harvest", "class_sweep"]
    assert pair.chunk_refs == [
        {"doc_id": "doc-1", "chunk_id": "chunk-1"},
        {"doc_id": "doc-2", "chunk_id": "chunk-2"},
    ]


def test_attribute_less_observations_with_distinct_role_evidence_are_preserved():
    index = _user_group_index()
    entries = [
        (
            _observation(
                evidenceKind="narrative",
                quote="Groups the user belongs to.",
                note="Membership association.",
            ),
            "chunk_harvest",
            None,
        ),
        (
            _observation(
                evidenceKind="narrative",
                quote="Groups the user administers.",
                note="Administrative association.",
            ),
            "chunk_harvest",
            None,
        ),
    ]

    pairs, _ = group_observations(entries, index)
    pair = next(iter(pairs.values()))

    assert [observation.quote for observation in pair.observations] == [
        "Groups the user belongs to.",
        "Groups the user administers.",
    ]


def test_exact_duplicate_attribute_less_observations_are_merged():
    index = _user_group_index()
    observation = _observation(
        evidenceKind="endpoint_path",
        quote="GET /Users/{id}/Groups",
        note="Sub-resource path exposes the link as an API surface.",
    )
    entries = [
        (observation, "endpoint_seed", [{"doc_id": "doc-1", "chunk_id": "chunk-1"}]),
        (observation.model_copy(), "chunk_harvest", [{"doc_id": "doc-2", "chunk_id": "chunk-2"}]),
    ]

    pairs, _ = group_observations(entries, index)
    pair = next(iter(pairs.values()))

    assert pair.observations == [observation]
    assert pair.sources == ["endpoint_seed", "chunk_harvest"]
    assert len(pair.chunk_refs) == 2


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


def test_reference_prefixed_type_resolves_to_the_target_class():
    """REST extraction encodes a $ref as type="reference NAME"; that must still seed a relation."""
    index = _user_group_index()
    payload = {"attributes": {"groups": {"type": "reference Group", "format": "reference"}}}

    observations = observations_from_attributes("User", payload, index)

    assert len(observations) == 1
    assert observations[0].target_class == "Group"
    assert observations[0].evidence_kind == "attribute_metadata"


def test_an_exact_class_name_wins_over_the_reference_marker():
    """The marker is a producer convention, not a naming rule: it must not merge distinct classes."""
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "Group", "confidence": "high"},
        {"name": "reference Group", "description": "A distinct class that starts with the marker"},
    )
    payload = {"attributes": {"groups": {"type": "reference Group", "format": "reference"}}}

    observations = observations_from_attributes("User", payload, index)

    assert observations[0].target_class == "reference Group"


def test_recursive_reference_attribute_seeds_a_self_relation():
    """Group.parentGroup -> Group is a hierarchy, and this schema evidence is its strongest source."""
    index = _user_group_index()
    payload = {"attributes": {"parentGroup": {"type": "Group", "format": "reference"}}}

    observations = observations_from_attributes("Group", payload, index)

    assert len(observations) == 1
    assert observations[0].source_class == "Group"
    assert observations[0].target_class == "Group"
    assert observations[0].source_attribute == "parentGroup"
    assert observations[0].evidence_kind == "attribute_metadata"


def test_recursive_embedded_attribute_stays_a_rejection_signal():
    """Recursion is classified by format like any other target, not excluded before it is read."""
    index = _index(
        {"name": "Group", "confidence": "high"},
        {"name": "GroupDetail", "confidence": "medium", "embedded": True},
    )
    payload = {
        "attributes": {
            "nested": {"type": "Group", "format": "embedded"},
            "detail": {"type": "GroupDetail", "format": "embedded"},
        }
    }

    observations = observations_from_attributes("Group", payload, index)

    assert [observation.evidence_kind for observation in observations] == [
        "embedded_metadata",
        "embedded_metadata",
    ]


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


def test_recursive_sub_resource_endpoint_becomes_an_observation():
    """A sub-resource path from a class to itself is how a hierarchy is exposed over REST."""
    index = _user_group_index()
    payload = {"endpoints": [{"path": "/Group/{id}/Group", "method": "GET"}]}

    observations = observations_from_endpoints("Group", payload, index)

    assert len(observations) == 1
    assert observations[0].source_class == "Group"
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


def test_unknown_attributes_are_not_grounded_without_observed_evidence():
    assert not is_attribute_grounded("anything", set())
    assert is_attribute_grounded("", {"known"})


def test_attribute_schema_states_distinguish_missing_invalid_empty_and_available():
    assert inspect_attribute_schema(None, present=False) == ("missing", set())
    assert inspect_attribute_schema(None, present=True) == ("invalid", set())
    assert inspect_attribute_schema({"attributes": None}, present=True) == ("invalid", set())
    assert inspect_attribute_schema({"attributes": {}}, present=True) == ("empty", set())
    assert inspect_attribute_schema({"attributes": {"groupIds": {}}}, present=True) == (
        "available",
        {"groupids"},
    )


# ==================== ASSOCIATION CLASSES ====================


def _membership_index() -> ObjectClassIndex:
    return _index(
        {"name": "User", "description": "An account", "confidence": "high"},
        {"name": "Group", "description": "An entitlement container", "confidence": "high"},
        {"name": "Membership", "description": "Links a user to a group", "confidence": "high"},
    )


_MEMBERSHIP_ATTRIBUTES = {
    "membership": {
        "attributes": {
            "user": {"type": "User", "format": "reference"},
            "group": {"type": "Group", "format": "reference"},
            "role": {"type": "string"},
            "validFrom": {"type": "string"},
        }
    }
}


def test_association_class_creates_the_pair_between_its_two_ends():
    """
    Without this, ``kind=link_object`` is unreachable from its own canonical evidence.

    ``Membership.user`` and ``Membership.group`` fold onto Membership|User and Group|Membership.
    The pair the domain needs, User|Group, is formed by no other stage, and adjudication may
    only name classes belonging to the pair it judges - so no call could ever return the
    association with Membership as the link.
    """
    index = _membership_index()
    entries = [
        (observation, "attribute_schema", None)
        for observation in observations_from_attributes("Membership", _MEMBERSHIP_ATTRIBUTES["membership"], index)
    ]

    synthetic, summary = expand_link_object_pairs(entries, index, _MEMBERSHIP_ATTRIBUTES)
    pairs, _ = group_observations(entries + synthetic, index)

    assert summary == ["Group|User via Membership"]
    assert "group|user" in pairs
    observation = pairs["group|user"].observations[0]
    assert observation.via_class == "Membership"
    # The attributes belong to Membership, not to either end; naming them here would hand
    # adjudication a name the grounding check would rightly reject.
    assert (observation.source_attribute, observation.target_attribute) == ("", "")


def test_a_class_that_is_mostly_plain_data_is_not_treated_as_an_association_class():
    """A user referencing a group and an org is not a link between them."""
    index = _index(
        {"name": "User", "confidence": "high"},
        {"name": "Group", "confidence": "high"},
        {"name": "Organization", "confidence": "high"},
    )
    payload = {
        "attributes": {
            "group": {"type": "Group", "format": "reference"},
            "org": {"type": "Organization", "format": "reference"},
            "name": {"type": "string"},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "title": {"type": "string"},
        }
    }
    entries = [(o, "attribute_schema", None) for o in observations_from_attributes("User", payload, index)]

    synthetic, summary = expand_link_object_pairs(entries, index, {"user": payload})

    assert synthetic == []
    assert summary == []


def test_association_class_expansion_respects_its_ceiling():
    index = _membership_index()
    entries = [
        (observation, "attribute_schema", None)
        for observation in observations_from_attributes("Membership", _MEMBERSHIP_ATTRIBUTES["membership"], index)
    ]

    with patch.object(config.digester, "relation_link_object_max_expanded_pairs", 0):
        assert expand_link_object_pairs(entries, index, _MEMBERSHIP_ATTRIBUTES) == ([], [])


# ==================== SEVERAL ASSOCIATIONS ON ONE PAIR ====================


def test_a_half_seen_second_association_keeps_the_pair_weak():
    """
    The reviewer's case: user is both a member and an owner of a group.

    Membership is documented from both ends, ownership only from the group side. The pair
    must still be re-read, or the ownership association never gets its other end - a plain
    "some attribute on each side" check would call this pair complete.
    """
    index = _user_group_index()
    entries = [
        (_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups"), "chunk_harvest", None),
        (_observation(sourceClass="Group", targetClass="User", sourceAttribute="members"), "chunk_harvest", None),
        (_observation(sourceClass="Group", targetClass="User", sourceAttribute="owners"), "chunk_harvest", None),
    ]

    pairs, _ = group_observations(entries, index)
    pair = next(iter(pairs.values()))

    group_side, user_side = pair.attributes_per_side()
    assert group_side == ["members", "owners"]
    assert user_side == ["groups"]
    assert pair.is_weak(), "an uneven attribute split means an association is still half-seen"


def test_a_pair_with_both_associations_complete_is_not_weak():
    index = _user_group_index()
    entries = [
        (_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups"), "chunk_harvest", None),
        (_observation(sourceClass="Group", targetClass="User", sourceAttribute="members"), "chunk_harvest", None),
        (_observation(sourceClass="User", targetClass="Group", sourceAttribute="ownedGroups"), "chunk_harvest", None),
        (_observation(sourceClass="Group", targetClass="User", sourceAttribute="owners"), "chunk_harvest", None),
    ]

    pairs, _ = group_observations(entries, index)
    assert not next(iter(pairs.values())).is_weak()


def test_attribute_breadth_outranks_repeated_evidence_for_one_association():
    """The adjudication ceiling should drop the pair worth fewer relations."""
    index = _user_group_index()

    two_associations, _ = group_observations(
        [
            (_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups"), "attribute_schema", None),
            (
                _observation(sourceClass="Group", targetClass="User", sourceAttribute="members"),
                "attribute_schema",
                None,
            ),
            (_observation(sourceClass="Group", targetClass="User", sourceAttribute="owners"), "attribute_schema", None),
        ],
        index,
    )
    one_association, _ = group_observations(
        [
            (_observation(sourceClass="User", targetClass="Group", sourceAttribute="groups"), "attribute_schema", None),
            (
                _observation(sourceClass="Group", targetClass="User", sourceAttribute="members"),
                "attribute_schema",
                None,
            ),
        ]
        * 2,
        index,
    )

    assert (
        next(iter(two_associations.values())).evidence_strength()
        > next(iter(one_association.values())).evidence_strength()
    )


def test_identical_display_names_on_one_pair_are_disambiguated():
    relations = [
        RelationRecord(
            name="a",
            display_name="User to Group",
            subject="user",
            subject_attribute="",
            object="group",
            object_attribute="members",
        ),
        RelationRecord(
            name="b",
            display_name="User to Group",
            subject="user",
            subject_attribute="",
            object="group",
            object_attribute="owners",
        ),
    ]

    labelled = disambiguate_relation_labels(relations)

    assert {relation.display_name for relation in labelled} == {
        "User to Group via members",
        "User to Group via owners",
    }


def test_a_single_relation_keeps_its_display_name_untouched():
    relations = [
        RelationRecord(
            name="a",
            display_name="User to Group",
            subject="user",
            subject_attribute="groups",
            object="group",
            object_attribute="members",
        )
    ]

    assert disambiguate_relation_labels(relations)[0].display_name == "User to Group"


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
