# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.chunk_filter.schema import ChunkFilterCriteria

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
