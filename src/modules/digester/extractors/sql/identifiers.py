# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Shared SQL identifier normalization for the digester SQL branch.

Both ``schema.py`` (raw DDL / JSON schema) and ``conndev_schema.py`` (conndev object-class and
SQL-table exports) join table and column records by their physical identity, so they must clean
identifiers the same way. This helper lives in its own module because ``schema.py`` imports
``conndev_schema.py`` and cannot also be imported by it.

The cleaning is deliberately conservative: strip surrounding whitespace, drop a schema/catalog
qualifier (``public.m_user`` -> ``m_user``), and remove the quoting characters used by the major
dialects (``"``, `````, ``[``, ``]``). It never changes case; callers casefold when they compare.
"""

from typing import Any


def clean_sql_identifier(value: Any) -> str:
    """Return the bare, unquoted identifier for ``value`` (empty string when there is none)."""
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.strip('"`[] ')
