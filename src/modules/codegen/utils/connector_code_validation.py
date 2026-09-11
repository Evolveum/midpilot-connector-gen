# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Format-aware validator for a generated or user-supplied connector artifact.

A connector artifact is declarative YAML, Groovy, or - in the operations the bundled
declarative-format reference documents as scripting hooks - YAML carrying embedded Groovy
expressions as string values (``objectExtractor: |\n  response.body()...``). Detection only
routes to the right syntax check; it never decides what the LLM generates. That choice is the
LLM's own, guided by the declarative-format documentation injected into its prompt (see
``src.modules.codegen.prompts.declarative_format_prompts``).

Detection is a single YAML parse attempt: every declarative document bundled with this
application is a YAML mapping, while the Groovy builder DSL's `identifier("...") { ... }`
call/brace syntax is not valid YAML and, on the rare input that does parse, yields a bare
scalar or sequence rather than a mapping. See test_connector_code_validation.py for the
empirical basis - no real Groovy sample tried there parses to a ``dict``.
"""

from enum import StrEnum
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ValidationError

from src.modules.codegen.utils.connector_yaml_schema import ConnectorYamlDocument
from src.modules.codegen.utils.groovy_validation import validate_groovy_code
from src.modules.codegen.utils.postprocess import strip_markdown_fences


class ConnectorCodeFormat(StrEnum):
    YAML = "yaml"
    GROOVY = "groovy"


class ConnectorCodeValidationError(ValueError):
    """Raised when a connector artifact (YAML or Groovy) fails validation."""


class _RejectDuplicateKeysLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that rejects a duplicate mapping key instead of silently keeping the last one."""


def _construct_mapping_rejecting_duplicates(
    loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_RejectDuplicateKeysLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_rejecting_duplicates,
)


def normalize_connector_code(code: str) -> str:
    """Strip Markdown fences and surrounding whitespace, whatever the format."""
    return strip_markdown_fences(code).strip()


def _load_single_yaml_document(code: str) -> Any:
    """
    Parse exactly one YAML document, rejecting duplicate keys and unsafe tags.

    ``yaml.load`` already raises ``ComposerError`` for more than one ``---``-separated
    document, and ``SafeLoader`` already refuses anything but the built-in YAML tags, so
    both requirements come from the loader itself rather than extra bookkeeping here.

    Used for *validation* once a document is already known to be YAML. Detection uses the
    lenient ``yaml.safe_load`` instead - see ``detect_connector_code_format`` for why.
    """
    return yaml.load(code, Loader=_RejectDuplicateKeysLoader)  # noqa: S506 - SafeLoader subclass, not full yaml.load


def detect_connector_code_format(code: str) -> ConnectorCodeFormat:
    """
    Classify normalized connector code as YAML or Groovy. Never raises.

    Deliberately uses the lenient ``yaml.safe_load`` rather than the duplicate-key-rejecting
    loader used for validation: a document that is YAML-*shaped* but violates one of our
    stricter validation rules (a duplicate key, say) must still be detected as YAML so that
    rule can actually reject it. Classifying a YAML-rules violation as "must be Groovy" risks
    silently accepting it under the unrelated Groovy grammar instead - Groovy's `label:
    statement` syntax makes a mapping-shaped snippet a surprisingly plausible parse.
    """
    normalized = normalize_connector_code(code)
    if not normalized:
        return ConnectorCodeFormat.GROOVY
    try:
        document = yaml.safe_load(normalized)
    except yaml.YAMLError:
        return ConnectorCodeFormat.GROOVY
    return ConnectorCodeFormat.YAML if isinstance(document, dict) else ConnectorCodeFormat.GROOVY


def validate_yaml_connector_code(code: str) -> Optional[str]:
    """
    Validate a declarative YAML connector artifact.

    Checks all documented configuration levels with strict types and rejects unknown
    keys. Only explicit script fields are parsed as Groovy; the artifact is never
    reserialized, preserving comments and formatting.

    Returns None when valid, otherwise a human-readable error message.
    """
    normalized = normalize_connector_code(code)
    if not normalized:
        return "Connector code cannot be empty"

    try:
        document = _load_single_yaml_document(normalized)
    except yaml.YAMLError as exc:
        return _clean_yaml_error_message(exc) or "Invalid YAML"

    if not isinstance(document, dict):
        return "Declarative connector YAML must have a mapping (key: value) document root"

    try:
        model = ConnectorYamlDocument.model_validate(document)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0]
        path = ".".join(map(str, error["loc"]))
        return f"{path}: {error['msg']}"
    return _validate_embedded_scripts(model)


def _validate_embedded_scripts(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, BaseModel):
        for name, field in type(value).model_fields.items():
            if name not in value.model_fields_set:
                continue
            child = getattr(value, name)
            child_path = f"{path}.{field.alias or name}".lstrip(".")
            metadata = field.json_schema_extra
            if isinstance(metadata, dict) and metadata.get("script"):
                if metadata.get("empty_body") and child == "EMPTY":
                    continue
                # Hooks are closure bodies; spec is a single build-time expression.
                wrapped = f"def hook = {{\n{child}\n}}"
                if metadata.get("expression"):
                    wrapped = f"def filterSpec = (\n{child}\n)"
                error = validate_groovy_code(wrapped)
                if error:
                    return f"{child_path}: {error}"
            else:
                error = _validate_embedded_scripts(child, child_path)
                if error:
                    return error
    elif isinstance(value, dict):
        for key, child in value.items():
            error = _validate_embedded_scripts(child, f"{path}.{key}")
            if error:
                return error
    elif isinstance(value, list):
        for index, child in enumerate(value):
            error = _validate_embedded_scripts(child, f"{path}[{index}]")
            if error:
                return error
    return None


def validate_connector_code(code: str) -> Optional[str]:
    """
    Validate a connector artifact, detecting whether it is declarative YAML or Groovy first.

    CPU-bound (YAML parsing and, for Groovy, ``groovy-parser`` tokenizing/parsing). Callers on
    an async execution path must run it in a worker thread - see the existing
    ``asyncio.to_thread`` use around the equivalent Groovy-only check in
    ``src.modules.codegen.connector_fix``.

    Returns None when valid, otherwise a human-readable error message.
    """
    normalized = normalize_connector_code(code)
    if detect_connector_code_format(normalized) is ConnectorCodeFormat.YAML:
        return validate_yaml_connector_code(normalized)
    return validate_groovy_code(normalized)


def ensure_valid_connector_code(code: str) -> str:
    """Return normalized connector code (YAML or Groovy) or raise with validation details."""
    normalized = normalize_connector_code(code)
    error = validate_connector_code(normalized)
    if error is not None:
        raise ConnectorCodeValidationError(error)
    return normalized


def _clean_yaml_error_message(exc: yaml.YAMLError) -> str:
    """
    Build a short, human-readable message from a PyYAML error.

    ``MarkedYAMLError`` (the base of every error the loader above can raise) carries the
    actual problem separately from its location markers; using ``.problem``/``.context``
    directly is more reliable than truncating ``str(exc)``, whose problem line is not always
    among the first few lines (e.g. a duplicate-key error leads with two location markers
    before naming the duplicate key).
    """
    problem = getattr(exc, "problem", None)
    context = getattr(exc, "context", None)
    parts = [part for part in (context, problem) if part]
    if parts:
        return ": ".join(parts)
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    return " ".join(lines[:3])
