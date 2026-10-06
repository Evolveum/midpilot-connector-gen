# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Documentation selection stored in a job input.

An extraction job that reads only part of a session's documentation stores that part
in its own input instead of re-reading the session when it runs:

- ``chunks`` holds every chunk the job may read, each stored once, in corpus order,
  with only the metadata extraction reads (:data:`SELECTED_CHUNK_METADATA_KEYS`);
- one reference list per :class:`SelectionRole` says which chunks each attempt uses,
  in the order the attempt reads them (corpus order when built by :meth:`from_corpus`).

The worker therefore sees exactly the documentation captured when the job was
prepared: a later upload cannot change a queued job, and a fallback attempt never
loads newer documentation. The cache identity covers only what the job can read
(see ``src.shared.normalize``), and cached relevance is remapped between the
selections stored in the source and the reusing job (:func:`build_selection_chunk_remap`).
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.shared.normalize import canonical_json, documentation_selection_chunk_identities, normalize_chunk_pair

logger = logging.getLogger(__name__)

DOCUMENTATION_SELECTION_VERSION: Literal[1] = 1
"""Bumped whenever the stored shape or its cache identity changes, so old jobs are never reused."""

SELECTED_CHUNK_METADATA_KEYS: tuple[str, ...] = ("content_type", "tags")
"""The only chunk metadata a selected chunk keeps: conndev/SQL classification and the prompt tags.

Extractors fed from a stored selection see ``@metadata`` with these keys and nothing else
(no ``category``, ``token_count``, ``filename``, ...); reading any other key yields nothing.
A leaf that needs another key must add it here, which also makes it part of the cache
identity, and bump :data:`DOCUMENTATION_SELECTION_VERSION` so older selections are not reused.
"""


class SelectionRole(StrEnum):
    """The part a chunk plays in a job; values are the reference-list keys in the stored input."""

    PRIMARY = "primary"
    """Chunks the first extraction attempt reads."""

    FALLBACK = "fallback"
    """Chunks a retry reads when the primary attempt finds nothing; never repeats a primary chunk."""

    SCIM_BASELINE = "scimBaseline"
    """midPoint connector-development documents the SCIM baseline is built from; the last
    definition of a conflicting schema, resource or class wins, so order is part of the identity."""

    SQL_SCHEMA = "sqlSchema"
    """The documentation SQL extraction joins physical schemas and conndev exports from; the
    first definition of a conflicting table wins, so order is part of the identity."""


class SelectionChunkReference(BaseModel):
    """A reference from a role to one stored chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    doc_id: str = Field(alias="docId", min_length=1)
    chunk_id: str = Field(alias="chunkId", min_length=1)


class SelectedChunk(BaseModel):
    """One documentation chunk captured for a job."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    chunk_id: str = Field(alias="chunkId", min_length=1)
    doc_id: str = Field(alias="docId", min_length=1)
    url: str | None = None
    summary: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_documentation_item(cls, item: Mapping[str, Any]) -> "SelectedChunk":
        """Capture a normalized documentation item (``@metadata`` or repository ``metadata`` shape)."""
        metadata = item.get("@metadata") or item.get("metadata") or {}
        return cls(
            chunk_id=str(item.get("chunkId") or ""),
            doc_id=str(item.get("docId") or ""),
            url=item.get("url"),
            summary=item.get("summary"),
            content=str(item.get("content") or ""),
            metadata={key: metadata[key] for key in SELECTED_CHUNK_METADATA_KEYS if key in metadata},
        )

    def reference(self) -> SelectionChunkReference:
        return SelectionChunkReference(doc_id=self.doc_id, chunk_id=self.chunk_id)

    def to_documentation_item(self) -> dict[str, Any]:
        """Return the normalized documentation-item shape the extractors consume.

        ``@metadata`` carries only :data:`SELECTED_CHUNK_METADATA_KEYS`.
        """
        return {
            "chunkId": self.chunk_id,
            "docId": self.doc_id,
            "url": self.url,
            "summary": self.summary,
            "content": self.content,
            "@metadata": dict(self.metadata),
        }


class InvalidDocumentationSelectionError(ValueError):
    """Raised when a stored selection references chunks it does not contain or is otherwise inconsistent."""


class DocumentationSelection(BaseModel):
    """The documentation a job may read and which chunks each of its attempts uses."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    version: Literal[1] = DOCUMENTATION_SELECTION_VERSION
    chunks: tuple[SelectedChunk, ...] = ()
    primary: tuple[SelectionChunkReference, ...] = ()
    fallback: tuple[SelectionChunkReference, ...] = ()
    scim_baseline: tuple[SelectionChunkReference, ...] = Field(default=(), alias="scimBaseline")
    sql_schema: tuple[SelectionChunkReference, ...] = Field(default=(), alias="sqlSchema")

    @model_validator(mode="after")
    def _references_resolve_to_stored_chunks(self) -> "DocumentationSelection":
        """Reject a selection a worker could not execute faithfully, before any LLM call."""
        chunks_by_id: dict[str, SelectedChunk] = {}
        for chunk in self.chunks:
            if chunk.chunk_id in chunks_by_id:
                raise InvalidDocumentationSelectionError(f"Chunk {chunk.chunk_id} is stored more than once")
            chunks_by_id[chunk.chunk_id] = chunk

        referenced: set[str] = set()
        for role in SelectionRole:
            seen: set[str] = set()
            for reference in self.references(role):
                target = chunks_by_id.get(reference.chunk_id)
                if target is None or target.doc_id != reference.doc_id:
                    raise InvalidDocumentationSelectionError(
                        f"Role {role.value} references chunk {reference.chunk_id} of document "
                        f"{reference.doc_id}, which the selection does not contain"
                    )
                if reference.chunk_id in seen:
                    raise InvalidDocumentationSelectionError(
                        f"Role {role.value} references chunk {reference.chunk_id} more than once"
                    )
                seen.add(reference.chunk_id)
            referenced |= seen

        unreferenced = sorted(set(chunks_by_id) - referenced)
        if unreferenced:
            raise InvalidDocumentationSelectionError(f"Chunks {unreferenced} are stored but no role references them")

        repeated = {reference.chunk_id for reference in self.primary} & {
            reference.chunk_id for reference in self.fallback
        }
        if repeated:
            raise InvalidDocumentationSelectionError(f"Fallback repeats primary chunks {sorted(repeated)}")
        return self

    @classmethod
    def from_corpus(
        cls,
        corpus: Sequence[Mapping[str, Any]],
        roles: Mapping[SelectionRole, Iterable[Mapping[str, Any]]],
    ) -> "DocumentationSelection":
        """
        Capture the chunks each role needs from one loaded corpus.

        ``roles`` maps a role to documentation items or ``{doc_id|docId, chunk_id|chunkId}``
        references. Chunks are stored once, in corpus order, and every role lists its
        references in that same order. A reference to a chunk the corpus does not hold
        (e.g. stale relevance of a deleted document) is dropped with a warning: it names
        documentation the job cannot read.
        """
        corpus_ids = [str(item.get("chunkId") or "") for item in corpus]
        position = {chunk_id: index for index, chunk_id in enumerate(corpus_ids) if chunk_id}

        role_chunk_ids: dict[SelectionRole, set[str]] = {}
        for role, references in roles.items():
            chunk_ids: set[str] = set()
            dangling = 0
            for reference in references:
                pair = normalize_chunk_pair(reference)
                if pair is None or pair[1] not in position:
                    dangling += 1
                    continue
                chunk_ids.add(pair[1])
            if dangling:
                logger.warning(
                    "[Documents:Selection] Dropped %d %s reference(s) to documentation the session no longer holds",
                    dangling,
                    role.value,
                )
            role_chunk_ids[role] = chunk_ids

        selected_ids = set().union(*role_chunk_ids.values()) if role_chunk_ids else set()
        chunks = [
            SelectedChunk.from_documentation_item(item)
            for item in corpus
            if str(item.get("chunkId") or "") in selected_ids
        ]

        def ordered_references(role: SelectionRole) -> tuple[SelectionChunkReference, ...]:
            wanted = role_chunk_ids.get(role, set())
            return tuple(chunk.reference() for chunk in chunks if chunk.chunk_id in wanted)

        return cls(
            chunks=tuple(chunks),
            primary=ordered_references(SelectionRole.PRIMARY),
            fallback=ordered_references(SelectionRole.FALLBACK),
            scim_baseline=ordered_references(SelectionRole.SCIM_BASELINE),
            sql_schema=ordered_references(SelectionRole.SQL_SCHEMA),
        )

    def references(self, role: SelectionRole) -> tuple[SelectionChunkReference, ...]:
        match role:
            case SelectionRole.PRIMARY:
                return self.primary
            case SelectionRole.FALLBACK:
                return self.fallback
            case SelectionRole.SCIM_BASELINE:
                return self.scim_baseline
            case SelectionRole.SQL_SCHEMA:
                return self.sql_schema

    def chunks_for(self, role: SelectionRole) -> list[SelectedChunk]:
        """Return the role's chunks in reference order, which is the order its consumer reads them in."""
        chunks_by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        return [chunks_by_id[reference.chunk_id] for reference in self.references(role)]

    def documentation_items(self, role: SelectionRole) -> list[dict[str, Any]]:
        """Return the role's chunks as normalized documentation items."""
        return [chunk.to_documentation_item() for chunk in self.chunks_for(role)]

    def relevant_chunks(self, role: SelectionRole) -> list[dict[str, str]]:
        """Return the role's references as internal ``{doc_id, chunk_id}`` relevance dicts."""
        return [{"doc_id": reference.doc_id, "chunk_id": reference.chunk_id} for reference in self.references(role)]

    def to_job_input(self) -> dict[str, Any]:
        """Serialize for ``jobs.input`` (camelCase keys, JSON values)."""
        return self.model_dump(by_alias=True, mode="json")


def build_selection_chunk_remap(
    previous: DocumentationSelection,
    current: DocumentationSelection,
) -> dict[str, dict[str, str]]:
    """
    Map each chunk of ``previous`` to the interchangeable chunk of ``current``.

    Chunks are interchangeable when their identity (content, extraction metadata, URL,
    summary and roles; see ``documentation_selection_chunk_identities``) is equal.
    Duplicate identities are paired in stored order, so the mapping is total whenever
    both selections share a cache fingerprint. Returns ``previous chunkId ->
    {"docId", "chunkId"}`` in the shape ``remap_reused_output_relevance`` expects.
    """
    current_chunks = {chunk.chunk_id: chunk for chunk in current.chunks}
    candidates: dict[str, list[SelectedChunk]] = defaultdict(list)
    for chunk_id, identity in documentation_selection_chunk_identities(current.to_job_input()):
        candidates[canonical_json(identity)].append(current_chunks[chunk_id])

    used: dict[str, int] = defaultdict(int)
    remap: dict[str, dict[str, str]] = {}
    for chunk_id, identity in documentation_selection_chunk_identities(previous.to_job_input()):
        key = canonical_json(identity)
        index = used[key]
        if index >= len(candidates.get(key, ())):
            continue
        used[key] += 1
        target = candidates[key][index]
        remap[chunk_id] = {"docId": target.doc_id, "chunkId": target.chunk_id}
    return remap
