"""
Merge utility functions for combining results from multiple documents.

This module provides sophisticated merging logic for different types of extraction results,
following the patterns established in the discovery module.
"""

#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from typing import Any, Dict, List


def _merge_first_non_empty_result(results: List[Dict[str, Any]], default: Dict[str, Any]) -> Dict[str, Any]:
    """Helper function to get the first non-empty result."""
    for result in results:
        if result and isinstance(result, dict):
            return result
    return default


def merge_auth_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge auth results from multiple documents.

    Combines all auth entries from different documents and deduplicates them based on:
    - name (normalized)
    - type (normalized)

    Merges quirks from duplicate entries.
    """
    seen: Dict[tuple, Dict[str, Any]] = {}

    for result in results:
        if isinstance(result, dict) and "auth" in result:
            for auth in result["auth"]:
                name_norm = (auth.get("name") or "").strip().lower()
                type_norm = (auth.get("type") or "").strip().lower()
                key = (name_norm, type_norm)

                if key not in seen:
                    seen[key] = auth
                else:
                    # Merge quirks if present
                    quirks = auth.get("quirks", "")
                    if quirks and quirks not in (seen[key].get("quirks") or ""):
                        existing_quirks = seen[key].get("quirks") or ""
                        seen[key]["quirks"] = f"{existing_quirks}; {quirks}" if existing_quirks else quirks

    return {"auth": list(seen.values())}


def merge_relations_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge relations results from multiple documents.

    Combines all relations from different documents and deduplicates them based on:
    - subject
    - subjectAttribute
    - object
    - objectAttribute
    """
    # Get all relations from all results
    all_relations = []
    for result in results:
        if isinstance(result, dict) and "relations" in result:
            all_relations.extend(result["relations"])

    # Deduplicate relations based on the specified parameters
    seen = set()
    unique_relations = []

    for rel in all_relations:
        # Create a unique key based on the specified parameters
        key = (
            rel.get("subject", ""),
            rel.get("subjectAttribute", ""),
            rel.get("object", ""),
            rel.get("objectAttribute", ""),
        )

        # Only add if we haven't seen this combination before
        if key not in seen:
            seen.add(key)
            unique_relations.append(rel)

    return {"relations": unique_relations}
