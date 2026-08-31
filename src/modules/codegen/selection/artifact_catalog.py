# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Catalog of the Groovy artifacts handled by an object-class connector fix.

Codegen stores each operation's script in its own ``{operationKey}Output``
session row. An object-class fix needs the opposite view: all generated scripts
of the selected class at once, each still carrying the identity of the operation
that produced it.

Keys are *derived* the same way they were written - from the extracted object
classes crossed with the fixed CRUD/search/schema operation set - rather than
discovered by scanning ``session_data`` for ``%Output``. A scan cannot recover
the operation identity reliably and the table holds many unrelated outputs.
Authorization and relation code remain outside the fix scope.

The trade-off is deliberate: code generated for an object class that has since
been dropped from ``objectClassesOutput`` is invisible here.
"""

import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Sequence
from uuid import UUID

from src.database.repositories.session_repository import SessionRepository
from src.documents.normalize import normalize_object_class_name
from src.modules.codegen.enums import ArtifactKind, SearchIntent, build_search_operation_key
from src.modules.codegen.selection.protocol_selectors import resolve_operation_docs_path
from src.modules.digester.errors import ObjectClassesNotFoundError, ObjectClassNotFoundError
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)

OBJECT_CLASSES_RESULT_KEY = "objectClassesOutput"

_OBJECT_CLASS_OPERATIONS: Sequence[tuple[ArtifactKind, str]] = (
    (ArtifactKind.NATIVE_SCHEMA, "NativeSchema"),
    (ArtifactKind.CONNID, "Connid"),
    (ArtifactKind.CREATE, "Create"),
    (ArtifactKind.UPDATE, "Update"),
    (ArtifactKind.DELETE, "Delete"),
)


@dataclass(frozen=True, kw_only=True)
class ConnectorArtifactSlot:
    """One place a connector can hold Groovy, whether or not it has been generated."""

    operation_key: str
    kind: ArtifactKind
    object_class: str | None = None
    intent: SearchIntent | None = None

    @property
    def session_key(self) -> str:
        return f"{self.operation_key}Output"


@dataclass(frozen=True, kw_only=True)
class ConnectorArtifact(ConnectorArtifactSlot):
    """A slot that has generated Groovy in it."""

    code: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ConnectorArtifact":
        """
        Rebuild an artifact from a job input payload.

        The exact inverse of :meth:`to_payload`, and kept beside it so the round trip
        the fix job depends on is one contract in one place.
        """
        intent = payload.get("intent")
        return cls(
            operation_key=payload["operationKey"],
            kind=ArtifactKind(payload["kind"]),
            object_class=payload.get("objectClass"),
            intent=SearchIntent(intent) if intent else None,
            code=payload["code"],
        )

    def with_code(self, code: str) -> "ConnectorArtifact":
        return replace(self, code=code)

    def to_payload(self) -> Dict[str, Any]:
        """Serialize for a job input payload and for the LLM prompt bundle."""
        payload: Dict[str, Any] = {
            "operationKey": self.operation_key,
            "kind": self.kind.value,
            "code": self.code,
        }
        if self.object_class is not None:
            payload["objectClass"] = self.object_class
        if self.intent is not None:
            payload["intent"] = self.intent.value
        return payload


def build_connector_artifact_slots(
    *,
    object_classes: Sequence[str],
) -> List[ConnectorArtifactSlot]:
    """
    Build every object-class session key handled by the fix.

    Object-class names are normalized first because that is how the generation
    endpoints built the keys; skipping it silently yields an empty catalog for a
    session whose classes were requested as ``User`` rather than ``user``.
    """
    slots: List[ConnectorArtifactSlot] = []

    for raw_name in object_classes:
        object_class = normalize_object_class_name(raw_name)
        if not object_class:
            continue
        for kind, suffix in _OBJECT_CLASS_OPERATIONS:
            slots.append(
                ConnectorArtifactSlot(
                    operation_key=f"{object_class}{suffix}",
                    kind=kind,
                    object_class=object_class,
                )
            )
        for intent in SearchIntent:
            slots.append(
                ConnectorArtifactSlot(
                    operation_key=build_search_operation_key(object_class, intent),
                    kind=ArtifactKind.SEARCH,
                    object_class=object_class,
                    intent=intent,
                )
            )

    return slots


async def load_connector_artifacts(
    repo: SessionRepository,
    session_id: UUID,
    object_class: str,
) -> List[ConnectorArtifact]:
    """
    Load every generated Groovy script for one object class, in catalog order.

    Two queries regardless of connector size: object classes and one bulk read of
    the selected class's derived CRUD/search/schema keys.

    :raises ObjectClassesNotFoundError: when the session has no extracted classes
    :raises ObjectClassNotFoundError: when the selected class is not in the extraction result
    """
    object_classes_output = await repo.get_session_value(session_id, OBJECT_CLASSES_RESULT_KEY)
    object_class_names = _extract_object_class_names(object_classes_output)
    if not object_class_names:
        raise ObjectClassesNotFoundError(session_id)

    normalized_object_class = normalize_object_class_name(object_class)
    known_object_classes = {normalize_object_class_name(name) for name in object_class_names}
    if normalized_object_class not in known_object_classes:
        raise ObjectClassNotFoundError(object_class, session_id)

    slots = build_connector_artifact_slots(object_classes=[normalized_object_class])
    stored = await repo.get_session_values(session_id, [slot.session_key for slot in slots])

    artifacts: List[ConnectorArtifact] = []
    for slot in slots:
        code = _extract_code(stored.get(slot.session_key))
        if code is None:
            continue
        artifacts.append(ConnectorArtifact(**vars(slot), code=code))

    logger.info(
        "[Codegen:Artifacts] Loaded %d generated script(s) from %d catalog slot(s) for object class %s",
        len(artifacts),
        len(slots),
        normalized_object_class,
    )
    return artifacts


def resolve_artifact_docs_paths(
    artifacts: Sequence[ConnectorArtifactSlot],
    protocol: ApiType,
) -> List[str]:
    """
    Collect the bundled DSL references for a set of artifacts, deduplicated.

    An artifact with no bundled reference is logged and skipped, never silently
    dropped: ``authorization`` on ``sql`` has no prompt family at all.
    """
    paths: List[str] = []
    seen: set[str] = set()
    for artifact in artifacts:
        docs_path = resolve_operation_docs_path(artifact.kind, protocol, intent=artifact.intent)
        if docs_path is None:
            logger.info(
                "[Codegen:Artifacts] No bundled DSL reference for %s on protocol %s",
                artifact.operation_key,
                protocol.value,
            )
            continue
        if docs_path in seen:
            continue
        seen.add(docs_path)
        paths.append(docs_path)
    return paths


def _extract_object_class_names(payload: Any) -> List[str]:
    if not isinstance(payload, Mapping):
        return []
    object_classes = payload.get("objectClasses")
    if not isinstance(object_classes, list):
        return []
    names: List[str] = []
    for entry in object_classes:
        if isinstance(entry, Mapping):
            name = entry.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name)
    return names


def _extract_code(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    if isinstance(code, str) and code.strip():
        return code
    return None
