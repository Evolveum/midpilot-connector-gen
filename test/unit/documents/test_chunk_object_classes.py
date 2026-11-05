# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any

from src.documents.normalize import merge_object_class_tags
from src.documents.processing.processor import tag_object_classes
from src.documents.processing.schema import LlmChunkOutput

CHUNK = """## Work packages

Use `GET /api/v3/work_packages` to list **work packages**.

| Attribute | Type |
|-----------|------|
| subject   | string |
"""


def _output(object_classes: list[Any], tags: list[str] | None = None) -> LlmChunkOutput:
    return LlmChunkOutput.model_validate(
        {
            "summary": "Work package reference.",
            "num_endpoints": 1,
            "tags": tags if tags is not None else ["REST"],
            "category": "reference_api",
            "object_classes": object_classes,
        }
    )


def test_object_class_with_a_verbatim_quote_naming_it_becomes_a_tag() -> None:
    output = _output([{"name": "WorkPackage", "evidence": "Use GET /api/v3/work_packages to list work packages."}])

    assert tag_object_classes(output, CHUNK, document="https://docs.example.test/wp").tags == ["REST", "WorkPackage"]


def test_shortened_or_reworded_quote_is_verified_by_the_identifier_it_names() -> None:
    chunk = (
        '{"paths":{"/api/v3/memberships":{"get":{}}},"enum":["User","Group"],"$ref":"#/components/schemas/RoleModel"}'
    )
    output = _output(
        [
            {"name": "Membership", "evidence": "`/api/v3/memberships` lists memberships"},
            {"name": "Group", "evidence": "enum:\n  - Group"},
            {"name": "Role", "evidence": "$ref: '#/components/schemas/RoleModel'"},
            {"name": "Principal", "evidence": '"enum":["User","Group"]'},  # names no Principal
        ]
    )

    assert tag_object_classes(output, chunk, document="spec.json").tags == ["REST", "Membership", "Group", "Role"]


def test_object_class_without_verifiable_evidence_is_dropped(caplog) -> None:
    output = _output(
        [
            {"name": "Project", "evidence": "GET /api/v3/projects"},  # not in the chunk
            {"name": "Status", "evidence": "| subject   | string |"},  # does not name the class
            {"name": "Role", "evidence": ""},
            "Version",  # a bare name has no evidence
        ]
    )

    with caplog.at_level("INFO"):
        tagged = tag_object_classes(output, CHUNK, document="https://docs.example.test/wp")

    assert tagged.tags == ["REST"]
    assert "Dropped 4 object classes" in caplog.text and "Project, Status, Role, Version" in caplog.text


def test_structural_classes_need_no_evidence_and_known_tags_are_not_repeated() -> None:
    output = _output([{"name": "WorkPackage", "evidence": "## Work packages"}], tags=["REST", "work_packages"])

    tagged = tag_object_classes(output, CHUNK, document="spec.json", structural_classes=["Work Packages", "Schema"])

    assert tagged.tags == ["REST", "work_packages", "Schema", "WorkPackage"]


def test_unnamed_object_class_entries_are_ignored_when_parsing() -> None:
    output = _output([{"evidence": "## Work packages"}, 42, None, {"name": "Task", "evidence": None}])

    assert [(mention.name, mention.evidence) for mention in output.object_classes] == [("Task", "")]
    assert _output(None).object_classes == []  # type: ignore[arg-type]


def test_merge_compares_normalized_forms_and_keeps_tag_order() -> None:
    assert merge_object_class_tags(["Users", "REST"], ["users", "Group", " group ", ""]) == ["Users", "REST", "Group"]
