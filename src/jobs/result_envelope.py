# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Internal fields carried beside a job's public result.

Some workers produce session state that must be published atomically with the primary
``*Output`` value but must never become part of the public API response. The companion
mapping travels through the job cache with the public result, then the persistence layer
splits it back into independent session keys in one transaction.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Dict

SESSION_COMPANION_OUTPUTS_KEY = "_sessionCompanionOutputs"


def get_session_companion_outputs(result: Any) -> Dict[str, Any]:
    """Return the validated companion-output mapping from a worker result."""
    if not isinstance(result, Mapping):
        return {}
    companions = result.get(SESSION_COMPANION_OUTPUTS_KEY)
    if not isinstance(companions, Mapping):
        return {}
    return {str(key): value for key, value in companions.items() if isinstance(key, str) and key}


def missing_session_companion_outputs(result: Any, required_keys: Sequence[str]) -> list[str]:
    """Return required companion keys absent from a cached or freshly produced result."""
    companions = get_session_companion_outputs(result)
    return [key for key in required_keys if key not in companions]


def strip_internal_result_fields(result: Any) -> Any:
    """Copy a job result without internal companion state for public status responses."""
    if not isinstance(result, dict) or SESSION_COMPANION_OUTPUTS_KEY not in result:
        return result
    public_result = dict(result)
    public_result.pop(SESSION_COMPANION_OUTPUTS_KEY, None)
    return public_result
