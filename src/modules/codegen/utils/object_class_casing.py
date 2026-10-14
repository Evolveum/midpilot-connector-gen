# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Restore the object-class spelling midPoint requested in a generated connector artifact.

midPoint matches object classes case-sensitively, while the model tends to follow the
PascalCase of the bundled examples (``objectClass("User")``). Only the declared object
class is rewritten, and only when it differs from the requested name by letter case
alone: another class name, an attribute, or a reference is never touched. YAML is edited
in place, never reserialized, and the edit is kept only when re-parsing proves that the
class key is the one thing that changed. Call off the event loop, like connector
validation.
"""

import json
import logging
import re
from typing import Any

import yaml

from src.modules.codegen.enums import ConnectorCodeFormat
from src.modules.codegen.utils.connector_code_validation import (
    _load_single_yaml_document,
    detect_connector_code_format,
)

logger = logging.getLogger(__name__)

_GROOVY_DECLARATION = re.compile(r"""(\bobjectClass\(\s*)(["'])([^"'\\\r\n]+)\2(\s*\))""")
_YAML_ROOT = re.compile(r"(?m)^objectClasses[ \t]*:")
_PLAIN_YAML_KEY = re.compile(r"[A-Za-z_][\w.-]*")
# A name that is unsafe inside a Groovy string literal or a YAML key is never written.
_UNSAFE_NAME_CHARACTERS = frozenset("\"'\\$\r\n")


def restore_object_class_casing(code: str, object_class: str) -> str:
    """Return ``code`` with its object-class declaration spelled exactly ``object_class``."""
    if not code.strip() or not object_class or _UNSAFE_NAME_CHARACTERS.intersection(object_class):
        return code
    if detect_connector_code_format(code) is ConnectorCodeFormat.YAML:
        return _restore_yaml(code, object_class)
    return _restore_groovy(code, object_class)


def _case_variant(names: list[str], object_class: str) -> str | None:
    """The single declared name that differs from ``object_class`` only by letter case."""
    if object_class in names:
        return None
    variants = {name for name in names if name.lower() == object_class.lower()}
    return variants.pop() if len(variants) == 1 else None


def _restore_groovy(code: str, object_class: str) -> str:
    variant = _case_variant([match.group(3) for match in _GROOVY_DECLARATION.finditer(code)], object_class)
    if variant is None:
        return code

    def _replace(match: re.Match[str]) -> str:
        if match.group(3) != variant:
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{object_class}{match.group(2)}{match.group(4)}"

    logger.info("[Codegen:ObjectClass] Restored requested object class spelling %s -> %s", variant, object_class)
    return _GROOVY_DECLARATION.sub(_replace, code)


def _restore_yaml(code: str, object_class: str) -> str:
    try:
        document = _load_single_yaml_document(code)
    except yaml.YAMLError:
        return code
    if not isinstance(document, dict) or not isinstance(document.get("objectClasses"), dict):
        return code
    variant = _case_variant([name for name in document["objectClasses"] if isinstance(name, str)], object_class)
    root = _YAML_ROOT.search(code)
    if variant is None or root is None:
        return code

    expected = {
        **document,
        "objectClasses": {
            (object_class if name == variant else name): value for name, value in document["objectClasses"].items()
        },
    }
    key = re.compile(rf"""(?<![\w$.-])(["']?){re.escape(variant)}\1(?=[ \t]*:)""")
    for match in key.finditer(code, root.end()):
        restored = code[: match.start()] + _yaml_key(object_class, match.group(1)) + code[match.end() :]
        if _parses_to(restored, expected):
            logger.info(
                "[Codegen:ObjectClass] Restored requested object class spelling %s -> %s", variant, object_class
            )
            return restored
    return code


def _yaml_key(name: str, quote: str) -> str:
    if quote:
        return f"{quote}{name}{quote}"
    # A JSON string is a valid YAML scalar for any name a plain key cannot carry.
    return name if _PLAIN_YAML_KEY.fullmatch(name) else json.dumps(name, ensure_ascii=False)


def _parses_to(code: str, expected: dict[Any, Any]) -> bool:
    try:
        return _load_single_yaml_document(code) == expected
    except yaml.YAMLError:
        return False
