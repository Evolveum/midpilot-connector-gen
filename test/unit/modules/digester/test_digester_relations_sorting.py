# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.digester.extractors.rest.relations import (
    _extract_relevant_names,
    _sort_relations_by_iga_priority,
)
from src.modules.digester.schema import RelationRecord


def _relation(
    *,
    subject: str,
    object_name: str,
    subject_attribute: str = "",
    object_attribute: str = "",
) -> RelationRecord:
    return RelationRecord(
        name=f"{subject}_to_{object_name}",
        display_name=f"{subject} to {object_name}",
        short_description="",
        subject=subject,
        subject_attribute=subject_attribute,
        object=object_name,
        object_attribute=object_attribute,
    )


def test_sort_relations_by_iga_priority_uses_object_class_order():
    relevant_payload = {
        "objectClasses": [
            {"name": "User", "description": "High priority", "confidence": "high"},
            {"name": "Group", "description": "Medium priority", "confidence": "medium"},
            {"name": "Role", "description": "Low priority", "confidence": "low"},
        ]
    }
    relations = [
        _relation(subject="role", object_name="user", subject_attribute="owner"),
        _relation(subject="group", object_name="role", subject_attribute="roles"),
        _relation(subject="user", object_name="group", subject_attribute="groups"),
    ]

    sorted_relations = _sort_relations_by_iga_priority(relations, relevant_payload)

    assert [(item.subject, item.object) for item in sorted_relations] == [
        ("user", "group"),
        ("group", "role"),
        ("role", "user"),
    ]


def test_sort_relations_by_iga_priority_keeps_unknown_classes_as_fallback():
    relevant_payload = {
        "objectClasses": [
            {"name": "User", "description": "High priority", "confidence": "high"},
            {"name": "Group", "description": "Medium priority", "confidence": "high"},
        ]
    }
    relations = [
        _relation(subject="foo", object_name="bar", subject_attribute="z"),
        _relation(subject="account", object_name="team", subject_attribute="a"),
        _relation(subject="user", object_name="group", subject_attribute="m"),
    ]

    sorted_relations = _sort_relations_by_iga_priority(relations, relevant_payload)

    assert [(item.subject, item.object) for item in sorted_relations] == [
        ("user", "group"),
        ("account", "team"),
        ("foo", "bar"),
    ]


def test_sort_relations_by_iga_priority_prefers_high_confidence_subjects():
    relevant_payload = {
        "objectClasses": [
            {"name": "Account", "description": "Account", "confidence": "medium"},
            {"name": "User", "description": "User", "confidence": "high"},
            {"name": "Project", "description": "Project", "confidence": "high"},
            {"name": "License", "description": "License", "confidence": "low"},
        ]
    }
    relations = [
        _relation(subject="account", object_name="user", subject_attribute="owner"),
        _relation(subject="license", object_name="user", subject_attribute="holder"),
        _relation(subject="project", object_name="account", subject_attribute="owner"),
        _relation(subject="user", object_name="project", subject_attribute="projects"),
    ]

    sorted_relations = _sort_relations_by_iga_priority(relations, relevant_payload)

    assert [(item.subject, item.object) for item in sorted_relations] == [
        ("user", "project"),
        ("project", "account"),
        ("account", "user"),
        ("license", "user"),
    ]


def test_extract_relevant_names_supports_camel_case_key():
    payload = {
        "objectClasses": [
            {"name": "User", "description": "User account", "confidence": "high"},
            {"name": "Group", "description": "User group", "confidence": "high"},
        ]
    }

    assert _extract_relevant_names(payload) == [
        ("User", "User account"),
        ("Group", "User group"),
    ]
