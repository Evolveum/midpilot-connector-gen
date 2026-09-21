# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""Canonical, non-executable explanations of native CRUD defaults.

Only recognize explicit empty artifacts. Missing output, customizations, unknown
structures and comments describing unmet requirements must retain their meaning.
Call off the event loop, like connector validation.
"""

import json
import re

import yaml

from src.modules.codegen.utils.connector_code_validation import _load_single_yaml_document

DEFAULTS_COMMENT = "No changes needed. Use framework defaults."


def normalize_operation_defaults(code: str, *, object_class: str, operation: str) -> str:
    if not code.strip():
        return code

    # Do not discard or misrepresent explanations, TODOs, or unsupported behavior.
    lines = code.splitlines()
    if any(("#" in line or "//" in line or "/*" in line) and DEFAULTS_COMMENT not in line for line in lines):
        return code

    try:
        document = _load_single_yaml_document(code)
    except (yaml.YAMLError, TypeError):
        document = None

    empty_documents: tuple[dict[str, object], ...] = (
        {},
        {"code": {}},
        {"objectClasses": {object_class: {}}},
        {"objectClasses": {object_class: {operation: {}}}},
    )
    if (isinstance(document, dict) and document in empty_documents) or re.fullmatch(r"\s*code:\s*\{\s*\}\s*", code):
        name = json.dumps(object_class, ensure_ascii=False)
        return f"# {DEFAULTS_COMMENT}\nobjectClasses:\n  {name}:\n    {operation}: {{}}"

    # Exact scope matching prevents renaming a different class or dropping behavior.
    name_pattern = (
        "(?:"
        + "|".join(re.escape(name) for name in (json.dumps(object_class, ensure_ascii=False), repr(object_class)))
        + ")"
    )
    empty_operation = rf"(?:{operation}\s*\{{\s*(?:(?:sql|scim)\s*\{{\s*\}}\s*)?\}}\s*)?"
    uncommented = code.replace(f"// {DEFAULTS_COMMENT}", "")
    if re.fullmatch(rf"\s*objectClass\(\s*{name_pattern}\s*\)\s*\{{\s*{empty_operation}\}}\s*", uncommented):
        # Escape interpolation as well as quotes in a Groovy double-quoted literal.
        name = json.dumps(object_class, ensure_ascii=False).replace("$", r"\$")
        return f"objectClass({name}) {{\n    {operation} {{\n        // {DEFAULTS_COMMENT}\n    }}\n}}"
    return code
