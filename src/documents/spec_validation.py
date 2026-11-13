# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Recognition of machine-readable API descriptions: OpenAPI 3, Swagger 2, SCIM schemas and JSON Schema.

The scraper keeps a fetched data file only when it is one of these formats, so a ``package.json`` or a
translation bundle served next to the documentation is never stored. The canonical hash identifies one
specification regardless of whether it was serialized as JSON or YAML.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

import yaml

SpecFormat = Literal["openapi", "swagger", "scim", "json_schema"]
Syntax = Literal["json", "yaml"]

_SCIM_URN_PREFIX = "urn:ietf:params:scim:"
# Top-level parts that say where an API is deployed, not what it is: an instance serving its own description lists
# itself there, so copies of one specification differ in them. ``basePath`` stays, it often carries the API version.
_DEPLOYMENT_KEYS: dict[SpecFormat, frozenset[str]] = {
    "openapi": frozenset({"servers"}),
    "swagger": frozenset({"host", "schemes"}),
}
# The libyaml loader parses large specifications many times faster than the pure-Python one.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class SpecDescription:
    """An API description; ``canonical_hash`` is equal for its JSON and YAML serializations and for copies that differ
    only in the servers they list."""

    format: SpecFormat
    title: str | None
    version: str | None
    canonical_hash: str


def parse_structured_document(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse JSON or YAML text; None for prose, a bare scalar or invalid input."""
    loaded = load_structured_document(text)
    return loaded[0] if loaded is not None else None


def load_structured_document(text: str) -> tuple[dict[str, Any] | list[Any], Syntax] | None:
    """Parse JSON or YAML text and tell which of the two it was; None for prose, a bare scalar or invalid input."""
    stripped = text.strip()
    if not stripped:
        return None
    syntax: Syntax = "json"
    try:
        document = json.loads(stripped)
    except ValueError:
        syntax = "yaml"
        try:
            document = yaml.load(stripped, Loader=_YAML_LOADER)
        except yaml.YAMLError:
            return None
    return (document, syntax) if isinstance(document, (dict, list)) else None


def describe_spec(document: dict[str, Any] | list[Any]) -> SpecDescription | None:
    """Identify the API-description format of a parsed document; None when it is none of them."""
    spec_format = detect_spec_format(document)
    if spec_format is None:
        return None
    info = document.get("info") if isinstance(document, dict) else None
    title = version = None
    if isinstance(info, dict):
        title = _optional_text(info.get("title"))
        version = _optional_text(info.get("version"))
    deployment_keys = _DEPLOYMENT_KEYS.get(spec_format, frozenset())
    if isinstance(document, dict) and deployment_keys:
        document = {key: value for key, value in document.items() if key not in deployment_keys}
    return SpecDescription(format=spec_format, title=title, version=version, canonical_hash=canonical_hash(document))


def canonical_hash(document: Any) -> str:
    """SHA-256 of the document's canonical JSON form, equal for the JSON and the YAML serialization of one spec."""
    serialized = json.dumps(_canonical_value(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def detect_spec_format(document: dict[str, Any] | list[Any]) -> SpecFormat | None:
    """Which API-description format a parsed document follows, without describing it further."""
    if isinstance(document, list):
        if document and all(isinstance(item, dict) and _is_scim_resource(item) for item in document):
            return "scim"
        return None
    # YAML reads an unquoted ``openapi: 3.1`` or ``swagger: 2.0`` as a number, hence the string comparison.
    if str(document.get("openapi", "")).startswith("3.") and any(
        isinstance(document.get(key), dict) for key in ("paths", "components", "webhooks")
    ):
        return "openapi"
    if str(document.get("swagger", "")) == "2.0" and isinstance(document.get("paths"), dict):
        return "swagger"
    if _is_scim_resource(document):
        return "scim"
    schema_uri = document.get("$schema")
    if isinstance(schema_uri, str) and "json-schema.org" in schema_uri:
        return "json_schema"
    if document.get("type") == "object" and isinstance(document.get("properties"), dict):
        return "json_schema"
    return None


def _is_scim_resource(document: dict[str, Any]) -> bool:
    """A SCIM message or resource (ListResponse, Schema, ResourceType) or an RFC 7643 style schema definition."""
    schemas = document.get("schemas")
    if isinstance(schemas, list) and any(isinstance(urn, str) and urn.startswith(_SCIM_URN_PREFIX) for urn in schemas):
        return True
    schema_id = document.get("id")
    return isinstance(schema_id, str) and schema_id.startswith("urn:") and isinstance(document.get("attributes"), list)


def _canonical_value(value: Any) -> Any:
    # YAML parses unquoted keys such as ``200:`` as integers and timestamps as datetime objects; JSON keeps
    # both as strings. Normalizing them makes the two serializations hash alike.
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
