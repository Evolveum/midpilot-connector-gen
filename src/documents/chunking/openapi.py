# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Structure-aware splitting of OpenAPI 3 and Swagger 2 descriptions.

A token split cuts an operation away from the schemas it references, so the digester reads an endpoint
without its attributes or a schema without its endpoints. A description that fits into one chunk is kept
whole; a larger one is split so that every chunk is itself a valid API description:

- the overview chunk holds the description-wide parts: the full ``info``, servers, security and security
  schemes, the tag list and every other top-level key;
- an operation chunk holds the operations of one or more resources (the first tag of an operation, else the
  first resource segment of its path) under a short header (title, version, servers), together with the
  transitive closure of the components they reference;
- components no operation chunk includes get chunks of their own, so nothing of the description is lost.

A resource too large for one chunk is split by path, then by operation. A referenced component that does
not fit next to its operation moves to a component chunk, and a component larger than the budget is split
into structurally valid fragments. Chunks keep the syntax and style of the source (YAML, indented or
minified JSON).
"""

import json
import logging
import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import unquote

import yaml

from src.documents.chunking.schema import split_structured_value
from src.documents.chunking.tokens import count_tokens
from src.documents.spec_validation import Syntax, detect_spec_format, load_structured_document

logger = logging.getLogger(__name__)

ComponentKey = tuple[str, ...]

_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
_SWAGGER_CONTAINERS: tuple[ComponentKey, ...] = (
    ("definitions",),
    ("parameters",),
    ("responses",),
    ("securityDefinitions",),
)
_SWAGGER_HEADER_KEYS = ("host", "basePath", "schemes", "consumes", "produces")
# Path segments that never name a resource: ``/api/v2/users/{id}`` belongs to ``users``.
_GENERIC_PATH_SEGMENTS = frozenset({"api", "rest"})
_VERSION_SEGMENT = re.compile(r"v\d+(\.\d+)*", re.IGNORECASE)

# Every shared node is written in full: an LLM reads the chunk, and YAML aliases would hide content from it.
_YamlDumper = type(
    "_YamlDumper",
    (getattr(yaml, "CSafeDumper", yaml.SafeDumper),),
    {"ignore_aliases": lambda _self, _data: True},
)


@dataclass(frozen=True)
class ApiDescriptionChunk:
    """A part of an API description that is itself a valid description."""

    text: str
    token_count: int
    operation_count: int
    object_classes: tuple[str, ...]


@dataclass(frozen=True)
class ApiDescriptionSplit:
    syntax: Syntax
    chunks: list[ApiDescriptionChunk]


@dataclass(frozen=True)
class _Unit:
    """Content kept together in one chunk: the operations of one path in one resource, or one component."""

    label: str
    resource: str | None
    paths: dict[str, dict[str, Any]]
    members: tuple[ComponentKey, ...]
    refs: tuple[ComponentKey, ...]
    operation_count: int
    tokens: int


def split_api_description(text: str, *, max_tokens: int) -> ApiDescriptionSplit | None:
    """Split an OpenAPI 3 or Swagger 2 description into valid sub-descriptions of at most ``max_tokens`` tokens.

    Returns None when the text is not such a description. CPU-bound.
    """
    loaded = load_structured_document(text)
    if loaded is None:
        return None
    document, syntax = loaded
    if not isinstance(document, dict):
        return None
    spec_format = detect_spec_format(document)
    if spec_format not in ("openapi", "swagger"):
        return None
    splitter = _ApiDescriptionSplitter(
        document, swagger=spec_format == "swagger", serialize=_serializer(text, syntax), max_tokens=max_tokens
    )
    return ApiDescriptionSplit(syntax=syntax, chunks=splitter.split(text.strip()))


def _serializer(text: str, syntax: Syntax) -> Callable[[Any], str]:
    """Serialize like the source: YAML stays YAML, minified JSON stays minified (a third fewer tokens)."""
    if syntax == "yaml":
        return lambda value: yaml.dump(value, Dumper=_YamlDumper, sort_keys=False, allow_unicode=True)
    if "\n" in text.strip():
        return lambda value: json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class _ApiDescriptionSplitter:
    def __init__(
        self, document: dict[str, Any], *, swagger: bool, serialize: Callable[[Any], str], max_tokens: int
    ) -> None:
        self._document = document
        self._swagger = swagger
        self._serialize = serialize
        self._max_tokens = max_tokens
        self._containers = _component_containers(document, swagger=swagger)
        self._schema_container: ComponentKey = ("definitions",) if swagger else ("components", "schemas")
        self._overview_container: ComponentKey = (
            ("securityDefinitions",) if swagger else ("components", "securitySchemes")
        )
        self._components: dict[ComponentKey, Any] = {
            (*container, str(name)): value
            for container in self._containers
            for name, value in _value_at(document, container).items()
        }
        self._tags = {
            tag["name"]: tag
            for tag in _as_list(document.get("tags"))
            if isinstance(tag, dict) and isinstance(tag.get("name"), str)
        }
        self._header = _header(document, swagger=swagger)
        self._header_tokens = self._count(self._header)
        self._direct_refs: dict[ComponentKey, tuple[ComponentKey, ...]] = {}
        self._component_tokens: dict[ComponentKey, int] = {}
        self._emitted: set[ComponentKey] = set()
        self._crowded_operations: list[str] = []

    def split(self, text: str) -> list[ApiDescriptionChunk]:
        token_count = count_tokens(text)
        if token_count <= self._max_tokens:
            # A description that fits into one chunk stays as it is.
            units = [unit for units in self._resource_units().values() for unit in units]
            units.extend(self._component_unit(key) for key in self._components if key[:-1] == self._schema_container)
            return [
                ApiDescriptionChunk(
                    text, token_count, sum(unit.operation_count for unit in units), self._classes(units)
                )
            ]
        chunks = self._overview_chunks()
        for batch in self._pack(list(self._resource_units().values())):
            chunks.extend(self._emit(batch))
        remaining = [self._component_unit(key) for key in self._components if key not in self._emitted]
        for batch in self._pack([remaining]):
            chunks.extend(self._emit(batch))
        logger.info(
            "[Documents:Chunking] Split API description into %s chunks (%s operations, %s components)",
            len(chunks),
            sum(chunk.operation_count for chunk in chunks),
            len(self._components),
        )
        if self._crowded_operations:
            logger.warning(
                "[Documents:Chunking] %s operations reference more components than fit next to them; the rest moved "
                "to component chunks (first: %s)",
                len(self._crowded_operations),
                ", ".join(self._crowded_operations[:3]),
            )
        return chunks

    # Units

    def _overview_chunks(self) -> list[ApiDescriptionChunk]:
        own_containers = {container[0] for container in self._containers if container != self._overview_container}
        handled = {"paths", "components"} if not self._swagger else {"paths", *own_containers}
        overview = dict(self._header)
        overview.update((key, value) for key, value in self._document.items() if key not in handled)
        components = self._document.get("components")
        if not self._swagger and isinstance(components, dict):
            shared_components = {
                kind: entries
                for kind, entries in components.items()
                if ("components", kind) == self._overview_container or ("components", kind) not in self._containers
            }
            if shared_components:
                overview["components"] = shared_components
        self._emitted.update(key for key in self._components if key[:-1] == self._overview_container)

        chunk = self._text_chunk(overview)
        if chunk.token_count <= self._max_tokens:
            return [chunk]
        fragments = split_structured_value(overview, max_tokens=self._max_tokens, serialize=self._serialize)
        logger.info("[Documents:Chunking] API description overview split into %s fragments", len(fragments))
        return [self._text_chunk(fragment) for fragment in fragments]

    def _resource_units(self) -> dict[str, list[_Unit]]:
        """Path units grouped by resource: the first tag of an operation, else the first resource path segment."""
        paths = self._document.get("paths")
        if not isinstance(paths, dict):
            return {}
        prefix_length = _common_prefix_length(str(path) for path in paths)
        resources: dict[str, dict[str, dict[str, Any]]] = {}
        for raw_path, item in paths.items():
            if not isinstance(item, dict):
                continue
            path = str(raw_path)
            methods = _operation_methods(item)
            if not methods:
                resources.setdefault(_path_resource(path, prefix_length), {})[path] = dict(item)
                continue
            shared = {key: value for key, value in item.items() if key not in methods}
            for method in methods:
                resource = _first_tag(item[method]) or _path_resource(path, prefix_length)
                resources.setdefault(resource, {}).setdefault(path, dict(shared))[method] = item[method]
        return {
            resource: [self._path_unit(path, path_item, resource) for path, path_item in resource_paths.items()]
            for resource, resource_paths in resources.items()
        }

    def _path_unit(self, path: str, path_item: dict[str, Any], resource: str) -> _Unit:
        methods = _operation_methods(path_item)
        return _Unit(
            label=", ".join(f"{method.upper()} {path}" for method in methods) or path,
            resource=resource,
            paths={path: path_item},
            members=(),
            refs=self._refs(path_item),
            operation_count=len(methods),
            tokens=self._count({path: path_item}),
        )

    def _operation_units(self, unit: _Unit) -> list[_Unit]:
        ((path, path_item),) = unit.paths.items()
        methods = _operation_methods(path_item)
        shared = {key: value for key, value in path_item.items() if key not in methods}
        return [
            self._path_unit(path, {**shared, method: path_item[method]}, unit.resource or path) for method in methods
        ]

    def _component_unit(self, key: ComponentKey) -> _Unit:
        return _Unit(
            label="/".join(key),
            resource=None,
            paths={},
            members=(key,),
            refs=self._direct(key),
            operation_count=0,
            tokens=self._component_size(key),
        )

    # Packing

    def _pack(self, groups: list[list[_Unit]]) -> list[list[_Unit]]:
        """Pack units into batches whose estimated chunk size stays within the budget.

        A group (the units of one resource) starts a new batch unless it fits whole into the current one, so
        small resources share a chunk and a resource is split only when it does not fit into a chunk of its own.
        """
        batches: list[list[_Unit]] = []
        batch: list[_Unit] = []
        included: set[ComponentKey] = set()
        packed: set[ComponentKey] = set()
        size = self._header_tokens
        for units in groups:
            if batch and size + self._added_size(units, included) > self._max_tokens:
                batches.append(batch)
                batch, included, size = [], set(), self._header_tokens
            for unit in units:
                if unit.members and all(member in packed for member in unit.members):
                    continue
                added = self._added_size([unit], included)
                if batch and size + added > self._max_tokens:
                    batches.append(batch)
                    batch, included, size = [], set(), self._header_tokens
                    added = self._added_size([unit], included)
                batch.append(unit)
                content = [*self._closure(unit.refs), *unit.members]
                included.update(content)
                packed.update(content)
                size += added
        if batch:
            batches.append(batch)
        return batches

    def _added_size(self, units: list[_Unit], included: set[ComponentKey]) -> int:
        """Estimated tokens the units add to a batch that already includes the ``included`` components."""
        members = {member for unit in units for member in unit.members}
        closure = self._closure(ref for unit in units for ref in unit.refs)
        return sum(unit.tokens for unit in units) + sum(
            self._component_size(key) for key in closure if key not in included and key not in members
        )

    # Chunks

    def _emit(self, batch: list[_Unit]) -> list[ApiDescriptionChunk]:
        members = list(dict.fromkeys(member for unit in batch for member in unit.members))
        closure = [key for key in self._closure(ref for unit in batch for ref in unit.refs) if key not in members]
        chunk = self._chunk(batch, [*members, *closure])
        if chunk.token_count <= self._max_tokens:
            self._emitted.update(members, closure)
            return [chunk]
        if len(batch) > 1:
            middle = len(batch) // 2
            return self._emit(batch[:middle]) + self._emit(batch[middle:])
        return self._emit_oversized(batch[0], closure)

    def _emit_oversized(self, unit: _Unit, closure: list[ComponentKey]) -> list[ApiDescriptionChunk]:
        if unit.operation_count > 1:
            return [chunk for batch in self._pack([self._operation_units(unit)]) for chunk in self._emit(batch)]
        if unit.members and self._header_tokens + unit.tokens > self._max_tokens:
            return self._emit_fragments(unit.members[0])

        budget = self._max_tokens - self._header_tokens - unit.tokens
        kept: list[ComponentKey] = []
        for key in closure:
            if self._component_size(key) <= budget:
                kept.append(key)
                budget -= self._component_size(key)
        chunk = self._chunk([unit], [*unit.members, *kept])
        # Component sizes are estimated without their indentation in the chunk.
        while chunk.token_count > self._max_tokens and kept:
            kept.pop()
            chunk = self._chunk([unit], [*unit.members, *kept])
        if chunk.token_count > self._max_tokens:
            logger.warning(
                "[Documents:Chunking] %s alone exceeds the chunk budget of %s tokens; keeping it whole",
                unit.label,
                self._max_tokens,
            )
        if len(kept) < len(closure):
            logger.debug(
                "[Documents:Chunking] %s of the components referenced by %s move to component chunks",
                len(closure) - len(kept),
                unit.label,
            )
            self._crowded_operations.append(unit.label)
        self._emitted.update(unit.members, kept)
        return [chunk]

    def _emit_fragments(self, key: ComponentKey) -> list[ApiDescriptionChunk]:
        """Split a component larger than the budget into fragments that each stay valid in its place."""

        def in_place(fragment: Any) -> dict[str, Any]:
            document = self._document_for([], [])
            _container_in(document, key[:-1])[key[-1]] = fragment
            return document

        # Sizes are measured in place, so each fragment fits the budget together with the header around it.
        fragments = split_structured_value(
            self._components[key],
            max_tokens=self._max_tokens,
            serialize=lambda value: self._serialize(in_place(value)),
        )
        logger.warning(
            "[Documents:Chunking] Component %s exceeds the chunk budget of %s tokens; split into %s fragments",
            "/".join(key),
            self._max_tokens,
            len(fragments),
        )
        self._emitted.add(key)
        object_classes = self._member_classes([key])
        return [self._text_chunk(in_place(fragment), object_classes) for fragment in fragments]

    def _document_for(self, batch: list[_Unit], components: list[ComponentKey]) -> dict[str, Any]:
        document = dict(self._header)
        resources = dict.fromkeys(unit.resource for unit in batch)
        tags = [self._tags[resource] for resource in resources if resource in self._tags]
        if tags:
            document["tags"] = tags
        paths: dict[str, dict[str, Any]] = {}
        for unit in batch:
            for path, path_item in unit.paths.items():
                paths[path] = {**paths.get(path, {}), **path_item}
        document["paths"] = paths
        for key in components:
            _container_in(document, key[:-1])[key[-1]] = self._components[key]
        return document

    def _chunk(self, batch: list[_Unit], components: list[ComponentKey]) -> ApiDescriptionChunk:
        chunk = self._text_chunk(self._document_for(batch, components), self._classes(batch))
        return replace(chunk, operation_count=sum(unit.operation_count for unit in batch))

    def _text_chunk(self, document: Any, object_classes: tuple[str, ...] = ()) -> ApiDescriptionChunk:
        text = self._serialize(document)
        return ApiDescriptionChunk(text, count_tokens(text), 0, object_classes)

    # Object classes

    def _classes(self, batch: list[_Unit]) -> tuple[str, ...]:
        """Names the chunk documents: its resources, the schemas their operations read or return, its own schemas."""
        names: dict[str, None] = {}
        for unit in batch:
            if unit.paths and unit.resource and any(character.isalnum() for character in unit.resource):
                names[unit.resource] = None
        names.update(dict.fromkeys(self._entry_schemas(ref for unit in batch if unit.paths for ref in unit.refs)))
        names.update(dict.fromkeys(self._member_classes(member for unit in batch for member in unit.members)))
        return tuple(names)

    def _entry_schemas(self, refs: Iterable[ComponentKey]) -> list[str]:
        """Schemas reached from ``refs`` through responses, request bodies and parameters, not through schemas."""
        names: dict[str, None] = {}
        seen: set[ComponentKey] = set()
        queue = deque(refs)
        while queue:
            key = queue.popleft()
            if key in seen:
                continue
            seen.add(key)
            if key[:-1] == self._schema_container:
                names[key[-1]] = None
            else:
                queue.extend(self._direct(key))
        return list(names)

    def _member_classes(self, members: Iterable[ComponentKey]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(key[-1] for key in members if key[:-1] == self._schema_container))

    # References

    def _refs(self, value: Any) -> tuple[ComponentKey, ...]:
        found: dict[ComponentKey, None] = {}
        queue = deque([value])
        while queue:
            item = queue.popleft()
            if isinstance(item, dict):
                ref = item.get("$ref")
                if isinstance(ref, str) and (key := self._ref_key(ref)) is not None:
                    found[key] = None
                queue.extend(item.values())
            elif isinstance(item, list):
                queue.extend(item)
        return tuple(found)

    def _ref_key(self, ref: str) -> ComponentKey | None:
        """The component a local reference points into: ``#/components/<kind>/<name>`` or ``#/definitions/<name>``."""
        if not ref.startswith("#/"):
            return None
        segments = tuple(unquote(segment).replace("~1", "/").replace("~0", "~") for segment in ref[2:].split("/"))
        for length in (3, 2):
            if len(segments) >= length and segments[:length] in self._components:
                return segments[:length]
        return None

    def _direct(self, key: ComponentKey) -> tuple[ComponentKey, ...]:
        if key not in self._direct_refs:
            self._direct_refs[key] = tuple(ref for ref in self._refs(self._components[key]) if ref != key)
        return self._direct_refs[key]

    def _closure(self, refs: Iterable[ComponentKey]) -> list[ComponentKey]:
        closure: dict[ComponentKey, None] = {}
        queue = deque(refs)
        while queue:
            key = queue.popleft()
            if key not in closure:
                closure[key] = None
                queue.extend(self._direct(key))
        return list(closure)

    def _component_size(self, key: ComponentKey) -> int:
        if key not in self._component_tokens:
            self._component_tokens[key] = self._count({key[-1]: self._components[key]})
        return self._component_tokens[key]

    def _count(self, value: Any) -> int:
        return count_tokens(self._serialize(value))


def _component_containers(document: dict[str, Any], *, swagger: bool) -> list[ComponentKey]:
    if swagger:
        return [container for container in _SWAGGER_CONTAINERS if isinstance(document.get(container[0]), dict)]
    components = document.get("components")
    if not isinstance(components, dict):
        return []
    return [("components", str(kind)) for kind, entries in components.items() if isinstance(entries, dict)]


def _header(document: dict[str, Any], *, swagger: bool) -> dict[str, Any]:
    """The parts every chunk repeats: format version, title and version, and where the API is served."""
    version_key = "swagger" if swagger else "openapi"
    header: dict[str, Any] = {version_key: document[version_key]}
    info = document.get("info")
    if isinstance(info, dict):
        header["info"] = {key: info[key] for key in ("title", "version") if key in info}
    for key in _SWAGGER_HEADER_KEYS if swagger else ("servers",):
        if key in document:
            header[key] = document[key]
    return header


def _operation_methods(path_item: dict[str, Any]) -> list[str]:
    return [key for key, value in path_item.items() if key in _HTTP_METHODS and isinstance(value, dict)]


def _first_tag(operation: dict[str, Any]) -> str | None:
    for tag in _as_list(operation.get("tags")):
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
    return None


def _path_segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _is_resource_segment(segment: str) -> bool:
    return not (
        segment.startswith("{")
        or segment.lower() in _GENERIC_PATH_SEGMENTS
        or _VERSION_SEGMENT.fullmatch(segment) is not None
    )


def _common_prefix_length(paths: Iterable[str]) -> int:
    """Number of leading segments all paths share, leaving at least one segment of the shortest path."""
    segment_lists = [segments for path in paths if (segments := _path_segments(path))]
    if not segment_lists:
        return 0
    limit = min(len(segments) for segments in segment_lists) - 1
    length = 0
    while length < limit and len({segments[length] for segments in segment_lists}) == 1:
        length += 1
    return length


def _path_resource(path: str, prefix_length: int) -> str:
    segments = _path_segments(path)[prefix_length:]
    return next((segment for segment in segments if _is_resource_segment(segment)), "/")


def _value_at(document: dict[str, Any], container: ComponentKey) -> dict[str, Any]:
    value: Any = document
    for key in container:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, dict) else {}


def _container_in(document: dict[str, Any], container: ComponentKey) -> dict[str, Any]:
    target = document
    for key in container:
        target = target.setdefault(key, {})
    return target


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
