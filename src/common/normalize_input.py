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
    if "session_id" in normalized_input:
        normalized_input.pop("session_id")
    if "doc_id" in normalized_input:
        normalized_input.pop("doc_id")
    if "usePreviousSessionData" in normalized_input:
        normalized_input.pop("usePreviousSessionData")
    if "chunks" in normalized_input:
        normalized_input["chunks"] = sorted(
            normalized_input["chunks"], key=lambda x: x[0] if isinstance(x, tuple) and len(x) > 0 else ""
        )
    if "documentationItems" in normalized_input:
        for doc_item in normalized_input["documentationItems"]:
            if isinstance(doc_item, dict):
                if "chunkId" in doc_item:
                    doc_item.pop("chunkId")
                if "chunk_id" in doc_item:
                    doc_item.pop("chunk_id")
                if "docId" in doc_item:
                    doc_item.pop("docId")
                if "doc_id" in doc_item:
                    doc_item.pop("doc_id")
                if "session_id" in doc_item:
                    doc_item.pop("session_id")
                if "scrape_job_ids" in doc_item:
                    doc_item.pop("scrape_job_ids")
                if "scrapeJobIds" in doc_item:
                    doc_item.pop("scrapeJobIds")
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
