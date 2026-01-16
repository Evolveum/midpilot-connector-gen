#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from enum import Enum

# Centralized enums for the micro-service


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"
    not_found = "not_found"


class JobStage(str, Enum):
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
    sorting = "sorting"
    sorting_finished = "sorting_finished"
    sorting_failed = "sorting_failed"
    schema_ready = "schema_ready"
    relations_ready = "relations_ready"
    resolving_duplicates = "resolving_duplicates"
    relevancy_filtering = "relevancy_filtering"
    relevancy_filtering_finished = "relevancy_filtering_finished"
    aggregation_finished = "aggregation_finished"
