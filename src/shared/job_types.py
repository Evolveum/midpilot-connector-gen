# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Background-job type vocabulary and the per-type execution rules.

Every job row stores one of the :class:`JobType` values in ``jobs.job_type``. The
behavior that depends on the type - output reuse, the documentation gate and the
progress counter names - is declared here once as plain data, so no caller has to
infer it from a substring of the type name.

The rules are pure data and live in ``shared`` because the database layer needs them
(documentation gate, progress mapping). Reading the configured reuse intervals is a
job-execution concern and stays in ``src.jobs.cache``.

Waiting for documentation is not a type rule: each scheduled job decides that itself
(``await_documentation``), and the queue reads it from ``jobs.documentation_wait_until``.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class JobType(StrEnum):
    """Every job type the service schedules; the values are persisted in ``jobs.job_type``."""

    DISCOVERY_CANDIDATE_LINKS = "discovery.getCandidateLinks"
    SCRAPE_RELEVANT_DOCUMENTATION = "scrape.getRelevantDocumentation"
    DOCUMENTATION_PROCESS_UPLOAD = "documentation.processUpload"
    DIGESTER_OBJECT_CLASSES = "digester.getObjectClass"
    DIGESTER_ATTRIBUTES = "digester.getObjectClassSchema"
    DIGESTER_ENDPOINTS = "digester.getEndpoints"
    DIGESTER_RELATIONS = "digester.getRelations"
    DIGESTER_CONNECTIVITY_ENDPOINT = "digester.getConnectivityEndpoint"
    DIGESTER_AUTH = "digester.getAuth"
    DIGESTER_INFO_METADATA = "digester.getInfoMetadata"
    CODEGEN_AUTHORIZATION = "codegen.getAuthorization"
    CODEGEN_NATIVE_SCHEMA = "codegen.getNativeSchema"
    CODEGEN_SEARCH = "codegen.getSearch"
    CODEGEN_CREATE = "codegen.getCreate"
    CODEGEN_UPDATE = "codegen.getUpdate"
    CODEGEN_DELETE = "codegen.getDelete"
    CODEGEN_RELATION = "codegen.getRelation"
    CODEGEN_FIX_CONNECTOR = "codegen.fixConnector"


class CacheWindow(StrEnum):
    """Which configured interval bounds the age of a reusable previous job."""

    DIGESTER_INPUT = "digesterInput"
    """``config.digester.digester_input_check_interval``."""

    DISCOVERY_INPUT = "discoveryInput"
    """``config.search.discovery_input_check_interval``."""


class CacheReuse(StrEnum):
    """How the output of a matching previous job is turned into this job's result."""

    UPLOAD_PUBLICATION = "uploadPublication"
    """Republish the source job's processed chunks as this upload's document."""

    SESSION_DOCUMENTATION_RELEVANCE = "sessionDocumentationRelevance"
    """Remap relevance references between the source session's and this job's documentation."""

    STORED_SELECTION_RELEVANCE = "storedSelectionRelevance"
    """Remap relevance references between the documentation selections stored in both job inputs."""

    RESULT_WITH_ERRORS = "resultWithErrors"
    """Copy the result and carry the source job's non-fatal errors over."""

    RESULT = "result"
    """Copy the result unchanged."""


class ProgressCounters(StrEnum):
    """Public names of the two progress counters in a job status response."""

    DOCUMENTS = "documents"
    """``processedDocuments`` / ``totalDocuments``."""

    ITERATIONS = "iterations"
    """``completedIterations`` / ``totalIterations``."""


@dataclass(frozen=True)
class JobCachePolicy:
    """Output reuse for a cacheable job type; a job's ``skipCache`` input still disables it."""

    window: CacheWindow
    reuse: CacheReuse


@dataclass(frozen=True)
class JobTypePolicy:
    """Everything that depends on a job's type."""

    cache: JobCachePolicy | None
    """``None`` means the type never reuses a previous job's output."""

    produces_documentation: bool
    """Jobs that wait for documentation wait for unfinished jobs of these types."""

    progress_counters: ProgressCounters


class UnknownJobTypeError(ValueError):
    """Raised for a job type that has no declared policy (e.g. a row from a removed job type)."""

    def __init__(self, job_type: object):
        super().__init__(f"Unknown job type {job_type!r}; no execution policy is declared for it")
        self.job_type = job_type


def _cached(window: CacheWindow, reuse: CacheReuse) -> JobTypePolicy:
    return JobTypePolicy(
        cache=JobCachePolicy(window=window, reuse=reuse),
        produces_documentation=False,
        progress_counters=ProgressCounters.DOCUMENTS,
    )


_DIGESTER_SESSION_RELEVANCE = _cached(CacheWindow.DIGESTER_INPUT, CacheReuse.SESSION_DOCUMENTATION_RELEVANCE)
_DIGESTER_SELECTION_RELEVANCE = _cached(CacheWindow.DIGESTER_INPUT, CacheReuse.STORED_SELECTION_RELEVANCE)
_CODEGEN = _cached(CacheWindow.DISCOVERY_INPUT, CacheReuse.RESULT_WITH_ERRORS)

JOB_TYPE_POLICIES: Mapping[JobType, JobTypePolicy] = MappingProxyType(
    {
        JobType.DISCOVERY_CANDIDATE_LINKS: _cached(CacheWindow.DISCOVERY_INPUT, CacheReuse.RESULT),
        # Scraping reuses documentation of a previous scrape inside its own worker.
        JobType.SCRAPE_RELEVANT_DOCUMENTATION: JobTypePolicy(
            cache=None,
            produces_documentation=True,
            progress_counters=ProgressCounters.ITERATIONS,
        ),
        JobType.DOCUMENTATION_PROCESS_UPLOAD: JobTypePolicy(
            cache=JobCachePolicy(window=CacheWindow.DISCOVERY_INPUT, reuse=CacheReuse.UPLOAD_PUBLICATION),
            produces_documentation=True,
            progress_counters=ProgressCounters.DOCUMENTS,
        ),
        JobType.DIGESTER_OBJECT_CLASSES: _DIGESTER_SESSION_RELEVANCE,
        JobType.DIGESTER_ATTRIBUTES: _DIGESTER_SELECTION_RELEVANCE,
        JobType.DIGESTER_ENDPOINTS: _DIGESTER_SELECTION_RELEVANCE,
        JobType.DIGESTER_RELATIONS: _DIGESTER_SESSION_RELEVANCE,
        JobType.DIGESTER_CONNECTIVITY_ENDPOINT: _DIGESTER_SESSION_RELEVANCE,
        JobType.DIGESTER_AUTH: _DIGESTER_SESSION_RELEVANCE,
        JobType.DIGESTER_INFO_METADATA: _DIGESTER_SESSION_RELEVANCE,
        JobType.CODEGEN_AUTHORIZATION: _CODEGEN,
        JobType.CODEGEN_NATIVE_SCHEMA: _CODEGEN,
        JobType.CODEGEN_SEARCH: _CODEGEN,
        JobType.CODEGEN_CREATE: _CODEGEN,
        JobType.CODEGEN_UPDATE: _CODEGEN,
        JobType.CODEGEN_DELETE: _CODEGEN,
        JobType.CODEGEN_RELATION: _CODEGEN,
        # A fix always works from the scripts and errors of this request.
        JobType.CODEGEN_FIX_CONNECTOR: JobTypePolicy(
            cache=None,
            produces_documentation=False,
            progress_counters=ProgressCounters.DOCUMENTS,
        ),
    }
)

DOCUMENTATION_PRODUCER_JOB_TYPES: tuple[JobType, ...] = tuple(
    job_type for job_type, policy in JOB_TYPE_POLICIES.items() if policy.produces_documentation
)


def parse_job_type(value: str) -> JobType:
    """Resolve a persisted job type, rejecting values that have no declared policy."""
    try:
        return JobType(value)
    except ValueError as exc:
        raise UnknownJobTypeError(value) from exc


def job_type_policy(job_type: JobType | str) -> JobTypePolicy:
    """Return the declared policy of a job type; unknown types are rejected, never defaulted."""
    resolved = job_type if isinstance(job_type, JobType) else parse_job_type(job_type)
    return JOB_TYPE_POLICIES[resolved]
