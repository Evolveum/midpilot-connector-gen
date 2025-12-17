# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List


def attributes_to_records_for_codegen(merged: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize attribute map into a sorted list of records the codegen prompts expect.
    Note: Accepts either 'updatable' or legacy 'updateable' keys; 'updatable' wins.
    """
    records: List[Dict[str, Any]] = []
    for norm_key, data in merged.items():
        records.append(
            {
                "name": data.get("name") or norm_key,
                "jsonType": data.get("type") or "",
                "openApiFormat": data.get("format") or "",
                "description": data.get("description") or "",
                "mandatory": bool(data.get("mandatory", False)),
                "updateable": bool(data.get("updatable", data.get("updateable", False))),
                "creatable": bool(data.get("creatable", False)),
                "readable": bool(data.get("readable", True)),
                "multivalue": bool(data.get("multivalue", False)),
                "returnedByDefault": bool(data.get("returnedByDefault", True)),
            }
        )
    records.sort(key=lambda r: str(r.get("name", "")).lower())
    return records
