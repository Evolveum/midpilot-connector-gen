# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for the connector artifact catalog."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.codegen.enums import ArtifactKind, SearchIntent
from src.modules.codegen.selection.artifact_catalog import (
    ConnectorArtifact,
    ConnectorArtifactSlot,
    build_connector_artifact_slots,
    load_connector_artifacts,
    resolve_artifact_docs_paths,
)
from src.modules.digester.errors import ObjectClassesNotFoundError, ObjectClassNotFoundError
from src.shared.enums import ApiType


def _slot_keys(**kwargs) -> list[str]:
    return [slot.session_key for slot in build_connector_artifact_slots(**kwargs)]


def test_slots_cover_every_operation_of_every_object_class():
    keys = _slot_keys(object_classes=["user", "group"])

    assert len(keys) == 16
    for object_class in ("user", "group"):
        for suffix in ("NativeSchema", "Connid", "Create", "Update", "Delete"):
            assert f"{object_class}{suffix}Output" in keys
        for suffix in ("SearchAll", "SearchFilter", "SearchId"):
            assert f"{object_class}{suffix}Output" in keys


def test_object_class_names_are_normalized_to_the_generated_key_form():
    """Generation endpoints normalize before building keys; skipping it yields an empty catalog."""
    keys = _slot_keys(object_classes=["User"])

    assert "userCreateOutput" in keys
    assert "UserCreateOutput" not in keys


def test_qualified_sql_object_class_is_preserved_in_artifact_keys():
    keys = _slot_keys(object_classes=["Database1.Schema_A.Users"])

    assert "database1.schema_a.usersNativeSchemaOutput" in keys
    assert "database1.schema_a.usersSearchAllOutput" in keys


def test_slots_skip_blank_names():
    keys = _slot_keys(object_classes=["user", "", "   "])

    assert len(keys) == 8


def test_search_slots_carry_their_intent():
    slots = build_connector_artifact_slots(object_classes=["user"])
    search_slots = [slot for slot in slots if slot.kind is ArtifactKind.SEARCH]

    assert {slot.intent for slot in search_slots} == set(SearchIntent)


@pytest.mark.asyncio
async def test_load_keeps_only_generated_slots_and_uses_two_queries():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_value = AsyncMock(return_value={"objectClasses": [{"name": "user"}]})
    repo.get_session_values = AsyncMock(
        return_value={
            "userCreateOutput": {"code": 'objectClass("user") { create { } }'},
            "userSearchAllOutput": {"code": 'objectClass("user") { search { } }'},
            "userUpdateOutput": {"code": "   "},  # generated but empty -> not an artifact
            "userDeleteOutput": "not-a-mapping",
        }
    )

    artifacts = await load_connector_artifacts(repo, session_id, "user")

    assert [a.operation_key for a in artifacts] == ["userCreate", "userSearchAll"]
    assert artifacts[0].kind is ArtifactKind.CREATE
    assert artifacts[0].object_class == "user"
    assert artifacts[1].intent is SearchIntent.ALL
    assert repo.get_session_value.await_count == 1
    repo.get_session_values.assert_awaited_once()
    assert len(repo.get_session_values.await_args.args[1]) == 8


@pytest.mark.asyncio
async def test_load_raises_when_the_session_has_no_object_classes():
    repo = MagicMock()
    repo.get_session_value = AsyncMock(return_value=None)
    repo.get_session_values = AsyncMock()

    with pytest.raises(ObjectClassesNotFoundError) as exc_info:
        await load_connector_artifacts(repo, uuid4(), "user")

    assert exc_info.value.status_code == 404
    repo.get_session_values.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_reads_only_slots_for_the_selected_object_class():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_value = AsyncMock(
        return_value={"objectClasses": [{"name": "user"}, {"name": "group"}, {"name": "role"}]}
    )
    repo.get_session_values = AsyncMock(
        return_value={
            "userCreateOutput": {"code": 'objectClass("user") { create { } }'},
            "groupCreateOutput": {"code": 'objectClass("group") { create { } }'},
        }
    )

    artifacts = await load_connector_artifacts(repo, session_id, "User")

    assert [artifact.operation_key for artifact in artifacts] == ["userCreate"]
    requested_keys = repo.get_session_values.await_args.args[1]
    assert requested_keys
    assert all(key.startswith("user") for key in requested_keys)


@pytest.mark.asyncio
async def test_load_rejects_an_object_class_missing_from_the_extraction_result():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_value = AsyncMock(return_value={"objectClasses": [{"name": "user"}]})
    repo.get_session_values = AsyncMock()

    with pytest.raises(ObjectClassNotFoundError):
        await load_connector_artifacts(repo, session_id, "group")

    repo.get_session_values.assert_not_awaited()


def test_docs_paths_are_deduplicated_across_object_classes():
    slots = build_connector_artifact_slots(object_classes=["user", "group"])

    paths = resolve_artifact_docs_paths(slots, ApiType.REST)

    assert len(paths) == len(set(paths))
    assert "rest/50-create.adoc" in paths
    assert "rest/30-attribute-to-connid-attributes.adoc" in paths


@pytest.mark.parametrize(
    "artifact",
    [
        ConnectorArtifact(operation_key="userConnid", kind=ArtifactKind.CONNID, object_class="user", code="code"),
        ConnectorArtifact(
            operation_key="userSearchFilter",
            kind=ArtifactKind.SEARCH,
            object_class="user",
            intent=SearchIntent.FILTER,
            code="code",
        ),
        ConnectorArtifact(operation_key="authorization", kind=ArtifactKind.AUTHORIZATION, code="code"),
    ],
    ids=["object-class", "search-intent", "no-object-class"],
)
def test_an_artifact_survives_the_job_input_round_trip(artifact):
    """The fix job serializes artifacts into its input and rebuilds them in the worker."""
    assert ConnectorArtifact.from_payload(artifact.to_payload()) == artifact


def test_connid_reference_is_protocol_independent():
    slots = [ConnectorArtifactSlot(operation_key="userConnid", kind=ArtifactKind.CONNID, object_class="user")]

    assert resolve_artifact_docs_paths(slots, ApiType.SQL) == ["rest/30-attribute-to-connid-attributes.adoc"]
