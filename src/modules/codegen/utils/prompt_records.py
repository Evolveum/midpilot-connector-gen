# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from collections.abc import Iterator
from typing import Any, Dict, List, Mapping

from src.common.utils.coerce import as_mapping
from src.modules.codegen.schema import AttributesPayload
from src.modules.digester.schemas import AttributeResponse


def _attribute_items(payload: AttributesPayload) -> Iterator[tuple[str, Any]]:
    if isinstance(payload, AttributeResponse):
        yield from (payload.attributes or {}).items()
        return

    if "attributes" in payload and isinstance(payload["attributes"], Mapping):
        yield from payload["attributes"].items()
        return

    yield from payload.items()


def _attribute_data(info: Any) -> Mapping[str, Any]:
    if hasattr(info, "model_dump"):
        return as_mapping(info.model_dump())

    return as_mapping(info)


def build_attribute_context_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Convert attributes into records for CRUD operation prompt context.

    Keeps the extracted attribute fields intact and only removes chunk
    provenance that is not useful inside the LLM prompt payload.
    """
    records: List[Dict[str, Any]] = []
    for name, info in _attribute_items(payload):
        item: Dict[str, Any] = {"name": name}
        item.update(strip_relevant_documentation_refs(_attribute_data(info)))
        records.append(item)
    return records


def build_attribute_mapping_records(payload: AttributesPayload) -> List[Dict[str, Any]]:
    """
    Convert attributes into records for ConnID/native-schema mapping prompts.

    Note: Accepts either 'updatable' or legacy 'updateable' keys; 'updatable' wins.
    """
    records: List[Dict[str, Any]] = []
    for norm_key, info in _attribute_items(payload):
        data = _attribute_data(info)
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


def strip_relevant_documentation_refs(record: Mapping[str, Any]) -> Dict[str, Any]:
    cleaned = dict(record)
    cleaned.pop("relevantDocumentations", None)
    cleaned.pop("relevant_documentations", None)
    return cleaned
