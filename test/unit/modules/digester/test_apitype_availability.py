# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.enums import DetectionSource, ProtocolAvailability
from src.modules.digester.extractors.apitype.availability import summarize_scim_availability
from src.modules.digester.schemas import ApiTypeSignalResult


def test_empty_signals_summarize_to_unknown():
    summary = summarize_scim_availability({})
    assert summary.status is ProtocolAvailability.UNKNOWN
    assert summary.required_plan == ""
    assert summary.sources == ()


def test_paid_outranks_available_when_sources_disagree():
    summary = summarize_scim_availability(
        {
            DetectionSource.KNOWLEDGE: ApiTypeSignalResult(
                supports_scim=True, scim_availability=ProtocolAvailability.AVAILABLE
            ),
            DetectionSource.WEB_SEARCH: ApiTypeSignalResult(
                supports_scim=True, scim_availability=ProtocolAvailability.PAID, required_plan="Enterprise"
            ),
        }
    )
    assert summary.status is ProtocolAvailability.PAID
    assert summary.required_plan == "Enterprise"
    assert set(summary.sources) == {DetectionSource.KNOWLEDGE, DetectionSource.WEB_SEARCH}


def test_required_plan_taken_from_paid_signal_only():
    summary = summarize_scim_availability(
        {
            DetectionSource.KNOWLEDGE: ApiTypeSignalResult(
                supports_scim=True, scim_availability=ProtocolAvailability.AVAILABLE
            ),
        }
    )
    assert summary.status is ProtocolAvailability.AVAILABLE
    assert summary.required_plan == ""
    assert summary.sources == (DetectionSource.KNOWLEDGE,)


def test_unknown_only_signals_contribute_no_sources():
    summary = summarize_scim_availability(
        {
            DetectionSource.KNOWLEDGE: ApiTypeSignalResult(supports_scim=False),
            DetectionSource.WEB_SEARCH: ApiTypeSignalResult(supports_scim=False),
        }
    )
    assert summary.status is ProtocolAvailability.UNKNOWN
    assert summary.sources == ()


def test_non_supporting_signal_availability_is_ignored():
    summary = summarize_scim_availability(
        {
            DetectionSource.KNOWLEDGE: ApiTypeSignalResult(
                supports_scim=False,
                scim_availability=ProtocolAvailability.PAID,
                required_plan="Enterprise",
            ),
            DetectionSource.WEB_SEARCH: ApiTypeSignalResult(
                supports_scim=True,
                scim_availability=ProtocolAvailability.AVAILABLE,
            ),
        }
    )

    assert summary.status is ProtocolAvailability.AVAILABLE
    assert summary.required_plan == ""
    assert summary.sources == (DetectionSource.WEB_SEARCH,)
