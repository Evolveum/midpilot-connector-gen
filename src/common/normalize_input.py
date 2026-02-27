import copy
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def normalize_input(input_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize job input for better querying
    """
    normalized_input = copy.deepcopy(input_payload)
    # Remove fields that are not relevant or harmful for job uniqueness checks
    if "sessionId" in normalized_input:
        normalized_input.pop("sessionId")
    if "usePreviousSessionData" in normalized_input:
        normalized_input.pop("usePreviousSessionData")
    if "documentationItems" in normalized_input:
        for doc_item in normalized_input["documentationItems"]:
            if isinstance(doc_item, dict):
                if "id" in doc_item:
                    doc_item.pop("id")
                if "uuid" in doc_item:
                    doc_item.pop("uuid")
                if "pageId" in doc_item:
                    doc_item.pop("pageId")
        normalized_input["documentationItems"] = sorted(
            normalized_input["documentationItems"],
            key=lambda x: (str(x.get("url") or ""), str(x.get("summary") or ""))
            if isinstance(x, dict)
            else (str(x), ""),
        )
    if "relevantObjectClasses" in normalized_input and "objectClasses" in normalized_input["relevantObjectClasses"]:
        for obj_class in normalized_input["relevantObjectClasses"]["objectClasses"]:
            obj_class.pop("relevantChunks")
    if "relevantChunks" in normalized_input:
        normalized_input.pop("relevantChunks")
    return normalized_input
