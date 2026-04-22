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
