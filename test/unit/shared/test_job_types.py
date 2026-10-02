# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest

from src.config import config
from src.jobs.cache import reuse_window
from src.shared.job_types import (
    DOCUMENTATION_PRODUCER_JOB_TYPES,
    JOB_TYPE_POLICIES,
    CacheReuse,
    CacheWindow,
    JobType,
    ProgressCounters,
    UnknownJobTypeError,
    job_type_policy,
    parse_job_type,
)

DIGESTER_TYPES = {job_type for job_type in JobType if job_type.value.startswith("digester.")}
CODEGEN_TYPES = {job_type for job_type in JobType if job_type.value.startswith("codegen.")}


def test_every_job_type_declares_a_policy_and_nothing_else_does():
    assert set(JOB_TYPE_POLICIES) == set(JobType)


def test_persisted_job_type_values_are_unchanged():
    assert {job_type.value for job_type in JobType} == {
        "discovery.getCandidateLinks",
        "scrape.getRelevantDocumentation",
        "documentation.processUpload",
        "digester.getObjectClass",
        "digester.getObjectClassSchema",
        "digester.getEndpoints",
        "digester.getRelations",
        "digester.getConnectivityEndpoint",
        "digester.getAuth",
        "digester.getInfoMetadata",
        "codegen.getAuthorization",
        "codegen.getNativeSchema",
        "codegen.getSearch",
        "codegen.getCreate",
        "codegen.getUpdate",
        "codegen.getDelete",
        "codegen.getRelation",
        "codegen.fixConnector",
    }


def test_scraping_and_connector_fix_never_reuse_cached_output():
    assert job_type_policy(JobType.SCRAPE_RELEVANT_DOCUMENTATION).cache is None
    assert job_type_policy(JobType.CODEGEN_FIX_CONNECTOR).cache is None


@pytest.mark.parametrize("job_type", sorted(DIGESTER_TYPES))
def test_digester_jobs_use_the_digester_reuse_window(job_type):
    policy = job_type_policy(job_type).cache
    assert policy is not None
    assert policy.window is CacheWindow.DIGESTER_INPUT


@pytest.mark.parametrize(
    "job_type",
    sorted(set(JobType) - DIGESTER_TYPES - {JobType.SCRAPE_RELEVANT_DOCUMENTATION, JobType.CODEGEN_FIX_CONNECTOR}),
)
def test_other_cacheable_jobs_keep_the_discovery_reuse_window(job_type):
    policy = job_type_policy(job_type).cache
    assert policy is not None
    assert policy.window is CacheWindow.DISCOVERY_INPUT


def test_reuse_modes_are_declared_explicitly_per_type():
    reuse = {job_type: policy.cache.reuse for job_type, policy in JOB_TYPE_POLICIES.items() if policy.cache}

    assert reuse[JobType.DOCUMENTATION_PROCESS_UPLOAD] is CacheReuse.UPLOAD_PUBLICATION
    assert reuse[JobType.DISCOVERY_CANDIDATE_LINKS] is CacheReuse.RESULT
    assert reuse[JobType.DIGESTER_ATTRIBUTES] is CacheReuse.STORED_SELECTION_RELEVANCE
    assert reuse[JobType.DIGESTER_ENDPOINTS] is CacheReuse.STORED_SELECTION_RELEVANCE
    for job_type in DIGESTER_TYPES - {JobType.DIGESTER_ATTRIBUTES, JobType.DIGESTER_ENDPOINTS}:
        assert reuse[job_type] is CacheReuse.SESSION_DOCUMENTATION_RELEVANCE
    for job_type in CODEGEN_TYPES - {JobType.CODEGEN_FIX_CONNECTOR}:
        assert reuse[job_type] is CacheReuse.RESULT_WITH_ERRORS


def test_documentation_producers_are_scraping_and_uploads():
    assert DOCUMENTATION_PRODUCER_JOB_TYPES == (
        JobType.SCRAPE_RELEVANT_DOCUMENTATION,
        JobType.DOCUMENTATION_PROCESS_UPLOAD,
    )


def test_only_scraping_reports_iteration_progress():
    iterations = {
        job_type
        for job_type, policy in JOB_TYPE_POLICIES.items()
        if policy.progress_counters is ProgressCounters.ITERATIONS
    }
    assert iterations == {JobType.SCRAPE_RELEVANT_DOCUMENTATION}


@pytest.mark.parametrize("value", ["codegen.getConnID", "digester.test", "scrape.getRelevantDocumentationV2", ""])
def test_unknown_job_types_are_rejected_instead_of_defaulted(value):
    with pytest.raises(UnknownJobTypeError):
        parse_job_type(value)
    with pytest.raises(UnknownJobTypeError):
        job_type_policy(value)


def test_reuse_windows_read_the_configured_intervals():
    assert reuse_window(CacheWindow.DIGESTER_INPUT) == config.digester.digester_input_check_interval
    assert reuse_window(CacheWindow.DISCOVERY_INPUT) == config.search.discovery_input_check_interval
