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

    assert len(keys) == 14
    for object_class in ("user", "group"):
        for suffix in ("NativeSchema", "Create", "Update", "Delete"):
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

    assert len(keys) == 7


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
    assert len(repo.get_session_values.await_args.args[1]) == 7


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
    # Reached through the native-schema slot: the ConnID mapping shares its script.
    assert paths.index("rest/25-user-schema.adoc") + 1 == paths.index("connid-attributes.adoc")


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
    """
    The fix job serializes artifacts into its input and rebuilds them in the worker.

    The ConnID case is deliberate: no slot builds that kind any more, but a fix job
    scheduled before the ConnID mapping moved into the native schema still rehydrates
    one from its persisted input. Removing ``ArtifactKind.CONNID`` from the enum would
    make those jobs raise on rehydration.
    """
    assert ConnectorArtifact.from_payload(artifact.to_payload()) == artifact


def test_connid_reference_is_still_resolvable_for_a_persisted_connid_artifact():
    """No slot builds this kind any more, but a fix job scheduled before the merge rehydrates one."""
    slots = [ConnectorArtifactSlot(operation_key="userConnid", kind=ArtifactKind.CONNID, object_class="user")]

    assert resolve_artifact_docs_paths(slots, ApiType.SQL) == ["connid-attributes.adoc"]


@pytest.mark.parametrize(
    ("protocol", "schema_docs_path"),
    [
        (ApiType.REST, "rest/25-user-schema.adoc"),
        (ApiType.SCIM, "scim/25-schema-customization.adoc"),
        (ApiType.SQL, "sql/schema-customization.adoc"),
    ],
)
def test_fix_docs_include_both_references_for_a_native_schema_artifact(protocol, schema_docs_path):
    """
    Order is part of the contract.

    ``_load_dsl_documentation`` concatenates these in sequence and the fix prompt tells
    the model the protocol's own schema reference outranks the ConnID one on syntax.
    For REST this is also the regression guard: ``rest/25-user-schema.adoc`` carries no
    ConnID content of its own, so dropping the second document would leave a merged
    native-schema script with no reference for the mapping it contains.
    """
    slots = [
        ConnectorArtifactSlot(operation_key="userNativeSchema", kind=ArtifactKind.NATIVE_SCHEMA, object_class="user")
    ]

    assert resolve_artifact_docs_paths(slots, protocol) == [schema_docs_path, "connid-attributes.adoc"]
