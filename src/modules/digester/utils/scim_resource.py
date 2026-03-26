# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, Optional


def extract_scim_resource_path(object_class_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract resource endpoint path from object class data when available.
    """
    for key in ("endpoint", "resourceEndpoint", "resourcePath", "path"):
        value = object_class_data.get(key)
        if isinstance(value, str) and value.strip():
            path = value.strip()
            return path if path.startswith("/") else f"/{path}"
    return None


def infer_scim_resource_path(object_class: str) -> str:
    """
    Best-effort SCIM resource path inference from object class name.
    """
    compact = "".join(object_class.strip().split())
    if not compact:
        return "/Resources"

    lower = compact.lower()
    if lower.endswith("s"):
        plural = compact
    elif lower.endswith("y") and len(compact) > 1 and compact[-2].lower() not in "aeiou":
        plural = compact[:-1] + "ies"
    else:
        plural = compact + "s"

    return f"/{plural}"
