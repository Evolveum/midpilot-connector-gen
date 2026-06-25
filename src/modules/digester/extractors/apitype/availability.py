# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
SCIM availability aggregation (signal #6).

Combines the SCIM availability verdicts of the documentation-free signals (LLM
knowledge, web search) into a single advisory summary. SCIM may exist for a product
yet require a paid/enterprise plan the customer might not have, so this is surfaced as
a caveat. For now the summary is only logged; it is intentionally NOT part of the API
response until the representation is agreed with the team.
"""

from dataclasses import dataclass
from typing import Mapping, Tuple

from src.common.enums import ScimAvailability, ScimSource
from src.modules.digester.schemas import ApiTypeSignalResult

_PRECEDENCE: dict[ScimAvailability, int] = {
    ScimAvailability.UNKNOWN: 0,
    ScimAvailability.AVAILABLE: 1,
    ScimAvailability.PAID: 2,
}


@dataclass(frozen=True)
class ScimAvailabilitySummary:
    """Aggregated SCIM availability across signals."""

    status: ScimAvailability
    required_plan: str
    sources: Tuple[ScimSource, ...]


def summarize_scim_availability(
    signals: Mapping[ScimSource, ApiTypeSignalResult],
) -> ScimAvailabilitySummary:
    """
    Aggregate per-signal SCIM availability into one summary.

    The status is the highest-precedence value across SCIM-confirming signals
    (paid > available > unknown). ``required_plan`` is taken from the first paid
    SCIM-confirming signal that names a plan. ``sources`` lists the signals that
    confirmed SCIM.
    """
    status = ScimAvailability.UNKNOWN
    required_plan = ""
    sources: list[ScimSource] = []

    for name, signal in signals.items():
        if not signal.supports_scim:
            continue

        sources.append(name)
        if _PRECEDENCE[signal.scim_availability] > _PRECEDENCE[status]:
            status = signal.scim_availability
        if signal.scim_availability is ScimAvailability.PAID and signal.required_plan and not required_plan:
            required_plan = signal.required_plan

    return ScimAvailabilitySummary(status=status, required_plan=required_plan, sources=tuple(sources))
