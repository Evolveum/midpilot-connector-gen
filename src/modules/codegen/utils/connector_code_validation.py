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

import logging
from dataclasses import dataclass
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ValidationError

from src.modules.codegen.connector_yaml_schema import ConnectorYamlDocument
from src.modules.codegen.enums import ConnectorCodeFormat
from src.modules.codegen.errors import ConnectorCodeValidationError
from src.modules.codegen.utils.groovy_validation import validate_groovy_code
from src.modules.codegen.utils.postprocess import strip_markdown_fences

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationDiagnostic:
    path: str
    message: str

    def describe(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


@dataclass(frozen=True)
class CodeValidationReport:
    """Local diagnostics, without changing the artifact or its acceptance rules."""

    errors: tuple[ValidationDiagnostic, ...] = ()
    warnings: tuple[ValidationDiagnostic, ...] = ()

    @property
    def first_error(self) -> str | None:
        return self.errors[0].describe() if self.errors else None

    @property
    def has_feedback(self) -> bool:
        return bool(self.errors or self.warnings)


def _error_report(message: str) -> CodeValidationReport:
    return CodeValidationReport(errors=(ValidationDiagnostic("", message),))


def log_validation_warnings(report: CodeValidationReport) -> None:
    for warning in report.warnings:
        logger.warning("[Codegen:Validation] %s at %s", warning.message, warning.path)


class _RejectDuplicateKeysLoader(yaml.SafeLoader):
    """Keep connector-specific mapping rules isolated from the global SafeLoader."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


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
    normalized = strip_markdown_fences(code)
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

    Checks known configuration fields with strict types and rejects unknown root
    keys. Unknown nested options produce log warnings, not validation errors: the
    connector runtime may support options absent from our local model. Only known
    script fields are parsed as Groovy; unknown options are not inspected. The
    artifact is never reserialized, preserving all options, comments and formatting.

    Returns None when valid, otherwise a human-readable error message.
    """
    report = _inspect_yaml_connector_code(code)
    log_validation_warnings(report)
    return report.first_error


def _inspect_yaml_connector_code(code: str) -> CodeValidationReport:
    normalized = strip_markdown_fences(code)
    if not normalized:
        return _error_report("Connector code cannot be empty")

    try:
        document = _load_single_yaml_document(normalized)
    except yaml.YAMLError as exc:
        return _error_report(_clean_yaml_error_message(exc) or "Invalid YAML")

    if not isinstance(document, dict):
        return _error_report("Declarative connector YAML must have a mapping (key: value) document root")

    if _has_cyclic_containers(document):
        return _error_report("Declarative connector YAML must not contain cyclic aliases")

    try:
        model = ConnectorYamlDocument.model_validate(document)
    except ValidationError as exc:
        return CodeValidationReport(
            errors=tuple(
                ValidationDiagnostic(".".join(map(str, error["loc"])), error["msg"])
                for error in exc.errors(include_url=False, include_input=False, include_context=False)
            )
        )
    errors: list[ValidationDiagnostic] = []
    warnings: list[ValidationDiagnostic] = []
    _inspect_embedded_scripts(model, errors, warnings)
    return CodeValidationReport(errors=tuple(errors), warnings=tuple(warnings))


def _has_cyclic_containers(document: Any) -> bool:
    """Detect ancestor references while allowing shared, acyclic YAML aliases."""
    active: set[int] = set()
    completed: set[int] = set()
    pending = [(document, False)]
    while pending:
        value, exiting = pending.pop()
        if not isinstance(value, (dict, list)):
            continue
        identity = id(value)
        if exiting:
            active.remove(identity)
            completed.add(identity)
            continue
        if identity in active:
            return True
        if identity in completed:
            continue
        active.add(identity)
        pending.append((value, True))
        children = value.values() if isinstance(value, dict) else value
        pending.extend((child, False) for child in children)
    return False


def _inspect_embedded_scripts(
    value: Any,
    errors: list[ValidationDiagnostic],
    warnings: list[ValidationDiagnostic],
    path: str = "",
) -> None:
    if isinstance(value, BaseModel):
        for key in value.model_extra or {}:
            warnings.append(
                ValidationDiagnostic(
                    f"{path}.{key}".lstrip("."),
                    "Unrecognized YAML option; preserved for connector runtime validation",
                )
            )
        for name, field in type(value).model_fields.items():
            if name not in value.model_fields_set:
                continue
            child = getattr(value, name)
            child_path = f"{path}.{field.alias or name}".lstrip(".")
            metadata = field.json_schema_extra
            if isinstance(metadata, dict) and metadata.get("script") and isinstance(child, str):
                if metadata.get("empty_body") and child == "EMPTY":
                    continue
                # Hooks are closure bodies; spec is a single build-time expression.
                wrapped = f"def hook = {{\n{child}\n}}"
                if metadata.get("expression"):
                    wrapped = f"def filterSpec = (\n{child}\n)"
                error = validate_groovy_code(wrapped)
                if error:
                    errors.append(ValidationDiagnostic(child_path, error))
            else:
                _inspect_embedded_scripts(child, errors, warnings, child_path)
    elif isinstance(value, dict):
        for key, child in value.items():
            _inspect_embedded_scripts(child, errors, warnings, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _inspect_embedded_scripts(child, errors, warnings, f"{path}[{index}]")


def validate_connector_code(code: str) -> Optional[str]:
    """
    Validate a connector artifact, detecting whether it is declarative YAML or Groovy first.

    CPU-bound (YAML parsing and, for Groovy, ``groovy-parser`` tokenizing/parsing). Callers on
    an async execution path must run it in a worker thread - see the existing
    ``asyncio.to_thread`` use around the equivalent Groovy-only check in
    ``src.modules.codegen.connector_fix``.

    Returns None when valid, otherwise a human-readable error message.
    """
    report = inspect_connector_code(code)
    log_validation_warnings(report)
    return report.first_error


def inspect_connector_code(code: str) -> CodeValidationReport:
    """Collect diagnostics without logging. Run this CPU-bound check in a worker thread."""
    normalized = strip_markdown_fences(code)
    if not normalized:
        return _error_report("Connector code cannot be empty")
    if detect_connector_code_format(normalized) is ConnectorCodeFormat.YAML:
        return _inspect_yaml_connector_code(normalized)
    error = validate_groovy_code(normalized)
    return _error_report(error) if error else CodeValidationReport()


def ensure_valid_connector_code(code: str) -> str:
    """Return normalized connector code (YAML or Groovy) or raise with validation details."""
    normalized = strip_markdown_fences(code)
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
