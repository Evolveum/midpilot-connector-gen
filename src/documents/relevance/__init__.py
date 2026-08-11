# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Relevance handling for extraction results.

``transforms`` holds the pure reference/payload transformations,
``persistence`` the DB-backed loading and hydration. The public API is
re-exported here so callers import from ``src.documents.relevance``.
"""

from src.documents.relevance.persistence import (
    hydrate_attributes_with_relevance,
    hydrate_auth_sequences_from_relevance,
    hydrate_endpoints_with_relevance,
    hydrate_object_classes_with_relevance,
    load_object_class_relevance_map,
    load_relevance_map_for_result,
)
from src.documents.relevance.transforms import (
    attribute_entity_key,
    build_chunk_ref_remap,
    build_endpoint_entity_key,
    extract_attribute_relevance_rows,
    extract_attributes_map,
    extract_endpoint_relevance_rows,
    extract_object_class_relevance_rows,
    extract_relevant_rows_for_storage,
    normalize_chunk_refs_for_storage,
    remap_reused_output_relevance,
    result_key_uses_endpoint_entities,
    strip_attributes_relevance,
    strip_endpoints_relevance,
    strip_object_class_relevance,
    strip_relevance_from_session_payload,
    unwrap_result_payload,
)

__all__ = [
    "attribute_entity_key",
    "build_chunk_ref_remap",
    "build_endpoint_entity_key",
    "extract_attribute_relevance_rows",
    "extract_attributes_map",
    "extract_endpoint_relevance_rows",
    "extract_object_class_relevance_rows",
    "extract_relevant_rows_for_storage",
    "hydrate_attributes_with_relevance",
    "hydrate_auth_sequences_from_relevance",
    "hydrate_endpoints_with_relevance",
    "hydrate_object_classes_with_relevance",
    "load_object_class_relevance_map",
    "load_relevance_map_for_result",
    "normalize_chunk_refs_for_storage",
    "remap_reused_output_relevance",
    "result_key_uses_endpoint_entities",
    "strip_attributes_relevance",
    "strip_endpoints_relevance",
    "strip_object_class_relevance",
    "strip_relevance_from_session_payload",
    "unwrap_result_payload",
]
