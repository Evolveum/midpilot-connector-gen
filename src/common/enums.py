# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import StrEnum


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"
    not_found = "not_found"


class JobStage(StrEnum):
    """Common stages for job progress. Keep names aligned with existing JSON values."""

    # Queueing / lifecycle
    queue = "queue"
    running = "running"
    failed = "failed"
    finished = "finished"

    # Processing phases
    processing = "processing"
    chunking = "chunking"
    processing_chunks = "processing_chunks"
    generating = "generating"

    # Domain-specific phases used by digester modules
    discovery = "discovery"
    discovery_finished = "discovery_finished"
    discovery_failed = "discovery_failed"
    deduplication = "deduplication"
    deduplication_finished = "deduplication_finished"
    deduplication_failed = "deduplication_failed"
    building = "building"
    building_finished = "building_finished"
    building_failed = "building_failed"
    sorting = "sorting"
    sorting_finished = "sorting_finished"
    sorting_failed = "sorting_failed"
    schema_ready = "schema_ready"
    relations_ready = "relations_ready"
    resolving_duplicates = "resolving_duplicates"
    relevancy_filtering = "relevancy_filtering"
    relevancy_filtering_finished = "relevancy_filtering_finished"
    aggregation_finished = "aggregation_finished"


class ApiType(StrEnum):
    REST = "rest"
    SCIM = "scim"
    SQL = "sql"


class ScimAvailability(StrEnum):
    """
    Whether a detected SCIM capability is generally usable by the customer or paid.

    Used as an advisory signal only (currently logged, not part of the API response):
    SCIM may exist for a product yet require a paid/enterprise plan that the customer
    might not have.
    """

    AVAILABLE = "available"
    PAID = "paid"
    UNKNOWN = "unknown"
