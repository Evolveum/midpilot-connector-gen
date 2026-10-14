# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Canonical, non-executable artifacts for operations that need no customization.

Explicit empty artifacts are recognized here and presented with an explanatory comment.
Comments describing unmet requirements (TODOs, unsupported behavior) retain their meaning
and are never rewritten. The builders also supply the artifact returned when generation
produced nothing: framework defaults where the protocol has them, otherwise a TODO
placeholder. Call off the event loop, like connector validation.
"""

import json
import re
from typing import Collection, Sequence

import yaml

from src.modules.codegen.enums import ConnectorCodeFormat
from src.modules.codegen.utils.connector_code_validation import _load_single_yaml_document

DEFAULTS_COMMENT = "No changes needed. Use framework defaults."


def _quoted(name: str) -> str:
    # A JSON string is a valid single-line YAML scalar and keeps comments on one line.
    return json.dumps(name, ensure_ascii=False)


def _scoped_empty_operation(object_class: str, operation: str) -> str:
    return f"objectClasses:\n  {_quoted(object_class)}:\n    {operation}: {{}}"


def operation_defaults_artifact(object_class: str, operation: str) -> str:
    return f"# {DEFAULTS_COMMENT}\n{_scoped_empty_operation(object_class, operation)}"


def rest_operation_placeholder_artifact(object_class: str, operation: str) -> str:
    return (
        f"# TODO: No {operation} behavior for {_quoted(object_class)} was found in the documentation. "
        "REST operations have no framework defaults; add an endpoint.\n"
        + _scoped_empty_operation(object_class, operation)
    )


def relation_defaults_artifact() -> str:
    return f"# {DEFAULTS_COMMENT}\n{{}}"


def rest_relation_placeholder_artifact(relation_name: str) -> str:
    name = _quoted(relation_name)
    return (
        f"# TODO: No relationship {name} was found in the documentation. "
        f"Declare a Groovy relationship({name}) block.\n{{}}"
    )


def authorization_defaults_artifact(namespace: str, notes: Sequence[str] = ()) -> str:
    lines = [f"# {DEFAULTS_COMMENT}", *(f"# {note}" for note in notes), "authentication:", f"  {namespace}: {{}}"]
    return "\n".join(lines)


def _has_foreign_comment(code: str) -> bool:
    # Do not discard or misrepresent explanations, TODOs, or unsupported behavior.
    return any(
        ("#" in line or "//" in line or "/*" in line) and DEFAULTS_COMMENT not in line for line in code.splitlines()
    )


def _is_uncommented_empty_yaml(code: str, empty_documents: Collection[object]) -> bool:
    if not code.strip() or _has_foreign_comment(code):
        return False
    try:
        document = _load_single_yaml_document(code)
    except (yaml.YAMLError, TypeError):
        return False
    return isinstance(document, dict) and document in empty_documents


def _empty_operation_format(code: str, *, object_class: str, operation: str) -> ConnectorCodeFormat | None:
    empty_documents: tuple[dict[str, object], ...] = (
        {},
        {"code": {}},
        {"objectClasses": {object_class: {}}},
        {"objectClasses": {object_class: {operation: {}}}},
    )
    if _is_uncommented_empty_yaml(code, empty_documents):
        return ConnectorCodeFormat.YAML
    if not code.strip() or _has_foreign_comment(code):
        return None
    if re.fullmatch(r"\s*code:\s*\{\s*\}\s*", code):
        return ConnectorCodeFormat.YAML

    # Exact scope matching prevents renaming a different class or dropping behavior.
    name_pattern = "(?:" + "|".join(re.escape(name) for name in (_quoted(object_class), repr(object_class))) + ")"
    empty_operation = rf"(?:{operation}\s*\{{\s*(?:(?:sql|scim)\s*\{{\s*\}}\s*)?\}}\s*)?"
    uncommented = code.replace(f"// {DEFAULTS_COMMENT}", "")
    if re.fullmatch(rf"\s*objectClass\(\s*{name_pattern}\s*\)\s*\{{\s*{empty_operation}\}}\s*", uncommented):
        return ConnectorCodeFormat.GROOVY
    return None


def is_empty_operation_artifact(code: str, *, object_class: str, operation: str) -> bool:
    """Whether the code is an explicit artifact that declares nothing for the operation."""
    return _empty_operation_format(code, object_class=object_class, operation=operation) is not None


def normalize_operation_defaults(code: str, *, object_class: str, operation: str) -> str:
    code_format = _empty_operation_format(code, object_class=object_class, operation=operation)
    if code_format is ConnectorCodeFormat.YAML:
        return operation_defaults_artifact(object_class, operation)
    if code_format is ConnectorCodeFormat.GROOVY:
        # Escape interpolation as well as quotes in a Groovy double-quoted literal.
        name = _quoted(object_class).replace("$", r"\$")
        return f"objectClass({name}) {{\n    {operation} {{\n        // {DEFAULTS_COMMENT}\n    }}\n}}"
    return code


def is_empty_relation_artifact(code: str) -> bool:
    return _is_uncommented_empty_yaml(code, ({},))


def is_empty_authorization_artifact(code: str, *, namespace: str) -> bool:
    return _is_uncommented_empty_yaml(code, ({}, {"authentication": {}}, {"authentication": {namespace: {}}}))
