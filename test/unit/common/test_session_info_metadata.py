# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.enums import ApiType
from src.common.utils.session_info_metadata import (
    is_sql_api,
    resolve_effective_api_type,
    resolve_session_api_type,
)


def test_resolve_session_api_type_defaults_to_rest():
    assert resolve_session_api_type([]) == ApiType.REST


def test_resolve_session_api_type_prefers_sql():
    assert resolve_session_api_type(["rest", "sql"]) == ApiType.SQL


def test_resolve_session_api_type_detects_scim_case_insensitively():
    assert resolve_session_api_type([" scim "]) == ApiType.SCIM


def test_is_sql_api_detects_sql_case_insensitively():
    assert is_sql_api([" sql "])


@pytest.mark.asyncio
async def test_resolve_effective_api_type_uses_override_without_db_lookup():
    """An explicit override wins and the session metadata is never read."""
    with patch(
        "src.common.utils.session_info_metadata.get_session_api_types", new_callable=AsyncMock
    ) as mock_get_api_types:
        result = await resolve_effective_api_type(uuid4(), ApiType.SCIM)

    assert result == ApiType.SCIM
    mock_get_api_types.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_effective_api_type_falls_back_to_session_metadata():
    """Without an override the protocol is derived from the stored apiType metadata."""
    with patch(
        "src.common.utils.session_info_metadata.get_session_api_types",
        new_callable=AsyncMock,
        return_value=["sql"],
    ) as mock_get_api_types:
        result = await resolve_effective_api_type(uuid4(), None)

    assert result == ApiType.SQL
    mock_get_api_types.assert_awaited_once()
