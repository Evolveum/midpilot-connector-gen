# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""The single source of "now" for the whole service.

Every timestamp column is ``TIMESTAMP(timezone=True)``, so every value compared
against one has to be timezone-aware. A bare ``datetime.now()`` returns the host's
local wall clock, which silently shifts any window computed from it by the host's
UTC offset - invisible on a UTC container and wrong everywhere else. Producing the
current time through this helper keeps that mistake out of reach.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
