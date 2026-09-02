# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Shared SQL identifier normalization for the digester SQL branch.

Both ``schema.py`` (raw DDL / JSON schema) and ``conndev_schema.py`` (conndev object-class and
SQL-table exports) join table and column records by their physical identity, so they must clean
identifiers the same way. This helper lives in its own module because ``schema.py`` imports
``conndev_schema.py`` and cannot also be imported by it.

The cleaning is deliberately conservative. A complete table reference may drop its
schema/catalog qualifier (``public.m_user`` -> ``m_user``), while identity components such as
``catalog`` and ``schema`` must retain their complete value. Both forms remove surrounding
whitespace and the quoting characters used by the major dialects (``"``, `````, ``[``, ``]``).
They never change case; callers casefold when they compare.
"""

from typing import Any


def clean_sql_identifier_component(value: Any) -> str:
    """Return one unquoted catalog/schema/table component without dropping qualifiers."""
    return str(value or "").strip().strip('"`[] ')


def clean_sql_identifier(value: Any) -> str:
    """Return the bare, unquoted identifier for ``value`` (empty string when there is none)."""
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return clean_sql_identifier_component(text)


def split_sql_table_identifier(value: Any) -> tuple[str, str, str]:
    """Return ``(catalog, schema, table)`` from the qualified forms the SQL parser accepts."""
    text = str(value or "").strip()
    if not text:
        return "", "", ""

    components = [clean_sql_identifier_component(component) for component in text.split(".")]
    components = [component for component in components if component]
    if len(components) >= 3:
        return components[-3], components[-2], components[-1]
    if len(components) == 2:
        return "", components[0], components[1]
    return "", "", components[0] if components else ""
