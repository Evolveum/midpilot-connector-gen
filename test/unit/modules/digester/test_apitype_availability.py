# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.enums import ScimAvailability
from src.modules.digester.apitype.availability import summarize_scim_availability
from src.modules.digester.schemas import ApiTypeSignalResult


def test_empty_signals_summarize_to_unknown():
    summary = summarize_scim_availability({})
    assert summary.status is ScimAvailability.UNKNOWN
    assert summary.required_plan == ""
    assert summary.sources == ()


def test_paid_outranks_available_when_sources_disagree():
    summary = summarize_scim_availability(
        {
            "knowledge": ApiTypeSignalResult(supports_scim=True, scim_availability=ScimAvailability.AVAILABLE),
            "web_search": ApiTypeSignalResult(
                supports_scim=True, scim_availability=ScimAvailability.PAID, required_plan="Enterprise"
            ),
        }
    )
    assert summary.status is ScimAvailability.PAID
    assert summary.required_plan == "Enterprise"
    assert set(summary.sources) == {"knowledge", "web_search"}


def test_required_plan_taken_from_paid_signal_only():
    summary = summarize_scim_availability(
        {
            "knowledge": ApiTypeSignalResult(supports_scim=True, scim_availability=ScimAvailability.AVAILABLE),
        }
    )
    assert summary.status is ScimAvailability.AVAILABLE
    assert summary.required_plan == ""
    assert summary.sources == ("knowledge",)


def test_unknown_only_signals_contribute_no_sources():
    summary = summarize_scim_availability(
        {
            "knowledge": ApiTypeSignalResult(supports_scim=False),
            "web_search": ApiTypeSignalResult(supports_scim=False),
        }
    )
    assert summary.status is ScimAvailability.UNKNOWN
    assert summary.sources == ()
