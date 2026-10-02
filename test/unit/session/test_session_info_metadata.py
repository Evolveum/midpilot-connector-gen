# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.schemas import (
    BaseAPIEndpoint,
    InfoMetadata,
    RestAvailabilityInfo,
    ScimAvailabilityInfo,
    SqlAvailabilityInfo,
)
from src.session.info_metadata import (
    extract_base_api_url,
    extract_database_name,
    get_session_connection_target,
    is_sql_api,
    resolve_effective_api_type,
    resolve_session_api_type,
)
from src.shared.enums import ApiType


def _stored(metadata: InfoMetadata) -> dict:
    """Wrap an InfoMetadata payload the way it is persisted under a session."""
    return {"infoMetadata": metadata.model_dump(by_alias=True)}


def _both_blocks_session() -> dict:
    """Stored metadata exposing both a REST and a SCIM base endpoint."""
    return _stored(
        InfoMetadata(
            api_type=[ApiType.REST, ApiType.SCIM],
            rest_availability=RestAvailabilityInfo(
                base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/", api_type=ApiType.REST)]
            ),
            scim_availability=ScimAvailabilityInfo(
                base_api_endpoint=[BaseAPIEndpoint(uri="https://h/scim/v2/", api_type=ApiType.SCIM)]
            ),
        )
    )


_REST_SESSION = _stored(
    InfoMetadata(
        api_type=[ApiType.REST],
        rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
    )
)
# A SCIM session whose SCIM block carries no endpoint of its own.
_SCIM_SESSION_WITH_ONLY_A_REST_BLOCK = _stored(
    InfoMetadata(
        api_type=[ApiType.SCIM],
        rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
    )
)
_BOTH_BLOCKS_SESSION = _both_blocks_session()
_SQL_SESSION = _stored(
    InfoMetadata(
        api_type=[ApiType.REST, ApiType.SQL],
        rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
        sql_availability=SqlAvailabilityInfo(database_name="hr_db"),
    )
)


@pytest.mark.parametrize(
    ("stored_api_types", "expected"),
    [
        ([], ApiType.REST),
        (["rest", "sql"], ApiType.SQL),
        ([" scim "], ApiType.SCIM),
    ],
    ids=["defaults-to-rest", "sql-outranks-rest", "scim-is-normalized"],
)
def test_resolve_session_api_type_normalizes_and_prioritizes(stored_api_types: list, expected: ApiType):
    assert resolve_session_api_type(stored_api_types) == expected


def test_is_sql_api_detects_sql_case_insensitively():
    assert is_sql_api([" sql "])
    assert not is_sql_api([" rest "])


@pytest.mark.parametrize(
    ("stored", "protocol", "expected"),
    [
        (_REST_SESSION, None, "https://h/api/v2/"),
        # SCIM is the resolved protocol when both REST and SCIM are present, so its block wins.
        (_BOTH_BLOCKS_SESSION, None, "https://h/scim/v2/"),
        (_BOTH_BLOCKS_SESSION, ApiType.REST, "https://h/api/v2/"),
        (_BOTH_BLOCKS_SESSION, ApiType.SCIM, "https://h/scim/v2/"),
        # Without an explicit protocol the other HTTP block is an acceptable fallback...
        (_SCIM_SESSION_WITH_ONLY_A_REST_BLOCK, None, "https://h/api/v2/"),
        # ...but an explicit protocol is a hard constraint and must not fall back.
        (_SCIM_SESSION_WITH_ONLY_A_REST_BLOCK, ApiType.SCIM, ""),
        (_stored(InfoMetadata(api_type=[ApiType.SQL])), None, ""),
        (None, None, ""),
    ],
    ids=[
        "rest-session-reads-rest-block",
        "scim-session-prefers-scim-block",
        "explicit-rest",
        "explicit-scim",
        "resolved-protocol-falls-back",
        "explicit-protocol-does-not-fall-back",
        "no-http-endpoints",
        "no-metadata",
    ],
)
def test_extract_base_api_url_resolves_the_block_of_the_effective_protocol(
    stored: dict | None, protocol: ApiType | None, expected: str
):
    assert extract_base_api_url(stored, protocol) == expected


@pytest.mark.parametrize(
    ("stored", "protocol", "expected"),
    [
        (_SQL_SESSION, None, "hr_db"),
        (_SQL_SESSION, ApiType.SQL, "hr_db"),
        (_SQL_SESSION, ApiType.REST, ""),
        (_SQL_SESSION, ApiType.SCIM, ""),
        (_stored(InfoMetadata(api_type=[ApiType.REST])), None, ""),
        (None, None, ""),
    ],
    ids=["sql-block", "explicit-sql", "explicit-rest", "explicit-scim", "non-sql-session", "no-metadata"],
)
def test_extract_database_name_is_scoped_to_the_sql_block(stored: dict | None, protocol: ApiType | None, expected: str):
    assert extract_database_name(stored, protocol) == expected


@pytest.mark.asyncio
async def test_resolve_effective_api_type_uses_override_without_db_lookup():
    """An explicit override wins and the session metadata is never read."""
    with patch("src.session.info_metadata.get_session_api_types", new_callable=AsyncMock) as mock_get_api_types:
        result = await resolve_effective_api_type(uuid4(), ApiType.SCIM)

    assert result == ApiType.SCIM
    mock_get_api_types.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_effective_api_type_falls_back_to_session_metadata():
    """Without an override the protocol is derived from the stored apiType metadata."""
    with patch(
        "src.session.info_metadata.get_session_api_types",
        new_callable=AsyncMock,
        return_value=["sql"],
    ) as mock_get_api_types:
        result = await resolve_effective_api_type(uuid4(), None)

    assert result == ApiType.SQL
    mock_get_api_types.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_session_connection_target_honors_explicit_protocol():
    session_id = uuid4()
    stored = _stored(
        InfoMetadata(
            api_type=[ApiType.REST, ApiType.SCIM, ApiType.SQL],
            rest_availability=RestAvailabilityInfo(
                base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/", api_type=ApiType.REST)]
            ),
            scim_availability=ScimAvailabilityInfo(
                base_api_endpoint=[BaseAPIEndpoint(uri="https://h/scim/v2/", api_type=ApiType.SCIM)]
            ),
            sql_availability=SqlAvailabilityInfo(database_name="hr_db"),
        )
    )

    with patch(
        "src.session.info_metadata.load_session_metadata",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        rest_target = await get_session_connection_target(session_id, protocol=ApiType.REST)
        scim_target = await get_session_connection_target(session_id, protocol=ApiType.SCIM)
        sql_target = await get_session_connection_target(session_id, protocol=ApiType.SQL)

    assert rest_target == ("https://h/api/v2/", "")
    assert scim_target == ("https://h/scim/v2/", "")
    assert sql_target == ("", "hr_db")
