# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Per-protocol availability aggregation.

Combines the availability verdicts of the documentation-free signals (LLM knowledge, web
search) into a single advisory summary per protocol. A protocol may exist for a product yet
require a paid/enterprise/partner plan the customer might not have, so this is surfaced as a
caveat on the protocol's availability block. SCIM and REST are summarized independently (each
from its own signals) but share the same precedence rule.
"""

from dataclasses import dataclass
from typing import Mapping, Tuple

from src.modules.digester.schemas import ApiTypeSignalResult, RestSignalResult
from src.shared.enums import DetectionSource, ProtocolAvailability

# Availability precedence shared by both protocol summaries: paid > available > unknown.
_AVAILABILITY_RANK: dict[ProtocolAvailability, int] = {
    ProtocolAvailability.UNKNOWN: 0,
    ProtocolAvailability.AVAILABLE: 1,
    ProtocolAvailability.PAID: 2,
}


@dataclass(frozen=True)
class ScimAvailabilitySummary:
    """Aggregated SCIM availability across signals."""

    status: ProtocolAvailability
    required_plan: str
    sources: Tuple[DetectionSource, ...]


@dataclass(frozen=True)
class RestAvailabilitySummary:
    """Aggregated REST availability across signals."""

    status: ProtocolAvailability
    required_plan: str
    sources: Tuple[DetectionSource, ...]


def summarize_scim_availability(
    signals: Mapping[DetectionSource, ApiTypeSignalResult],
) -> ScimAvailabilitySummary:
    """
    Aggregate per-signal SCIM availability into one summary.

    The status is the highest-precedence value across SCIM-confirming signals
    (paid > available > unknown). ``required_plan`` is taken from the first paid
    SCIM-confirming signal that names a plan. ``sources`` lists the signals that
    confirmed SCIM.
    """
    status = ProtocolAvailability.UNKNOWN
    required_plan = ""
    sources: list[DetectionSource] = []

    for name, signal in signals.items():
        if not signal.supports_scim:
            continue

        sources.append(name)
        if _AVAILABILITY_RANK[signal.scim_availability] > _AVAILABILITY_RANK[status]:
            status = signal.scim_availability
        if signal.scim_availability is ProtocolAvailability.PAID and signal.required_plan and not required_plan:
            required_plan = signal.required_plan

    return ScimAvailabilitySummary(status=status, required_plan=required_plan, sources=tuple(sources))


def summarize_rest_availability(
    signals: Mapping[DetectionSource, RestSignalResult],
) -> RestAvailabilitySummary:
    """
    Aggregate per-signal REST availability into one summary.

    The status is the highest-precedence value across REST-confirming signals
    (paid > available > unknown). ``required_plan`` is taken from the first paid
    REST-confirming signal that names a plan. ``sources`` lists the signals that
    confirmed REST.
    """
    status = ProtocolAvailability.UNKNOWN
    required_plan = ""
    sources: list[DetectionSource] = []

    for name, signal in signals.items():
        if not signal.supports_rest:
            continue

        sources.append(name)
        if _AVAILABILITY_RANK[signal.availability] > _AVAILABILITY_RANK[status]:
            status = signal.availability
        if signal.availability is ProtocolAvailability.PAID and signal.required_plan and not required_plan:
            required_plan = signal.required_plan

    return RestAvailabilitySummary(status=status, required_plan=required_plan, sources=tuple(sources))
