# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.documents.filtering.schema import ChunkFilterCriteria

DEFAULT_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
    ],
)

METADATA_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
    ],
    # Also keep chunks whose category is outside the spec set but that are tagged as a relevant
    # API protocol, so apiType/metadata extraction does not miss SCIM/REST/provisioning evidence.
    category_override_tags=[
        "scim",
        "rest",
        "sql",
        "db",
        "provisioning",
    ],
)

DEFAULT_AUTH_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "overview",
    ],
    allowed_tags=[
        [
            "authentication",
            "auth",
            "authorization",
        ]
    ],
)

EXTENDED_AUTH_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "overview",
        "reference_api",
    ],
    allowed_tags=[
        [
            "authentication",
            "auth",
            "authorization",
        ]
    ],
)

ENDPOINT_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=1,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
    ],
)

CONNECTIVITY_ENDPOINT_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=1,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
        "overview",
    ],
)

CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA = ChunkFilterCriteria(
    min_length=None,
    min_endpoints_num=None,
    allowed_categories=[
        "spec_yaml",
        "spec_json",
        "reference_api",
        "overview",
    ],
)


def attribute_object_class_criteria(object_class: str) -> ChunkFilterCriteria:
    """Primary chunks for attribute extraction: DEFAULT_CRITERIA chunks tagged with ``object_class``."""
    return DEFAULT_CRITERIA.model_copy(update={"object_class_tags": [object_class]})


def endpoint_object_class_criteria(object_class: str) -> ChunkFilterCriteria:
    """Primary chunks for endpoint extraction: ENDPOINT_CRITERIA chunks (at least one endpoint) tagged with ``object_class``.

    No ``endpoint``/``endpoints`` tag is required: chunk processing rarely emits it, and ``min_endpoints_num``
    already guarantees the chunk documents an endpoint.
    """
    return ENDPOINT_CRITERIA.model_copy(update={"object_class_tags": [object_class]})
