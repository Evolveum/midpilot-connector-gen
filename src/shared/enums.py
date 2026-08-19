# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Product-wide vocabulary shared across domains and feature modules."""

from enum import StrEnum


class ApiType(StrEnum):
    REST = "rest"
    SCIM = "scim"
    SQL = "sql"


class GenerationIntent(StrEnum):
    """
    Business-domain lens object-class detection is judged through.

    Does not change which documentation is read or how extraction runs mechanically; it
    changes what counts as a primary object class when object-class detection assigns
    confidence and ranks results (see
    ``src/modules/digester/prompts/object_class_intents.py``). ``MANAGEMENT_ITSM``
    treats both domains as primary. ``MANAGEMENT`` is the default when a caller does
    not specify an intent.
    """

    MANAGEMENT = "management"
    ITSM = "itsm"
    MANAGEMENT_ITSM = "management_itsm"


class ProtocolAvailability(StrEnum):
    """
    Whether a detected integration protocol is generally usable by the customer or gated.

    Shared advisory signal for any detected protocol (SCIM, REST): a product may expose a
    protocol yet gate it behind a paid/enterprise/partner plan the customer might not have.
    """

    AVAILABLE = "available"
    PAID = "paid"
    UNKNOWN = "unknown"


class DetectionSource(StrEnum):
    """
    Where a protocol confirmation came from.

    Shared provenance for the per-protocol availability advisories (which signals confirmed a
    protocol). Declaration order is the order sources are reported in. Python StrEnums cannot be
    extended with extra members via subclassing, so this single enum carries every source and
    ``SCIM_CLOUD`` is a SCIM-only member (no other protocol has a dedicated registry signal).
    """

    SCIM_CLOUD = "scim_cloud"
    DOCUMENTATION = "documentation"
    KNOWLEDGE = "knowledge_of_llm"
    WEB_SEARCH = "web_search"


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
