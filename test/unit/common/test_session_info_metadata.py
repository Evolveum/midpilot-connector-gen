# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.common.enums import ApiType
from src.common.utils.session_info_metadata import (
    extract_base_api_url,
    extract_database_name,
    get_session_connection_target,
    is_sql_api,
    resolve_effective_api_type,
    resolve_session_api_type,
)
from src.modules.digester.schemas import (
    BaseAPIEndpoint,
    InfoMetadata,
    RestAvailabilityInfo,
    ScimAvailabilityInfo,
    SqlAvailabilityInfo,
)


def _stored(metadata: InfoMetadata) -> dict:
    """Wrap an InfoMetadata payload the way it is persisted under a session."""
    return {"infoMetadata": metadata.model_dump(by_alias=True)}


def test_resolve_session_api_type_defaults_to_rest():
    assert resolve_session_api_type([]) == ApiType.REST


def test_resolve_session_api_type_prefers_sql():
    assert resolve_session_api_type(["rest", "sql"]) == ApiType.SQL


def test_resolve_session_api_type_detects_scim_case_insensitively():
    assert resolve_session_api_type([" scim "]) == ApiType.SCIM


def test_is_sql_api_detects_sql_case_insensitively():
    assert is_sql_api([" sql "])


def test_extract_base_api_url_reads_rest_block_for_rest_session():
    stored = _stored(
        InfoMetadata(
            api_type=[ApiType.REST],
            rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
        )
    )
    assert extract_base_api_url(stored) == "https://h/api/v2/"


def test_extract_base_api_url_prefers_scim_block_for_scim_session():
    stored = _stored(
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
    # SCIM is the resolved protocol when both REST and SCIM are present, so its block wins.
    assert extract_base_api_url(stored) == "https://h/scim/v2/"


def test_extract_base_api_url_honors_explicit_protocol():
    stored = _both_blocks_session()

    assert extract_base_api_url(stored, ApiType.REST) == "https://h/api/v2/"
    assert extract_base_api_url(stored, ApiType.SCIM) == "https://h/scim/v2/"


def test_extract_base_api_url_with_explicit_protocol_does_not_fallback():
    stored = _stored(
        InfoMetadata(
            api_type=[ApiType.SCIM],
            rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
        )
    )

    assert extract_base_api_url(stored, ApiType.SCIM) == ""


def test_extract_base_api_url_falls_back_to_other_http_block():
    # SCIM session but the SCIM block has no endpoint -> fall back to the REST block.
    stored = _stored(
        InfoMetadata(
            api_type=[ApiType.SCIM],
            rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
        )
    )
    assert extract_base_api_url(stored) == "https://h/api/v2/"


def test_extract_base_api_url_empty_when_no_endpoints():
    assert extract_base_api_url(_stored(InfoMetadata(api_type=[ApiType.SQL]))) == ""
    assert extract_base_api_url(None) == ""


def test_extract_database_name_reads_sql_block():
    stored = _stored(InfoMetadata(api_type=[ApiType.SQL], sql_availability=SqlAvailabilityInfo(database_name="hr_db")))
    assert extract_database_name(stored) == "hr_db"


def test_extract_database_name_honors_explicit_protocol():
    stored = _stored(
        InfoMetadata(
            api_type=[ApiType.REST, ApiType.SQL],
            rest_availability=RestAvailabilityInfo(base_api_endpoint=[BaseAPIEndpoint(uri="https://h/api/v2/")]),
            sql_availability=SqlAvailabilityInfo(database_name="hr_db"),
        )
    )

    assert extract_database_name(stored, ApiType.REST) == ""
    assert extract_database_name(stored, ApiType.SCIM) == ""
    assert extract_database_name(stored, ApiType.SQL) == "hr_db"


def test_extract_database_name_empty_for_non_sql():
    assert extract_database_name(_stored(InfoMetadata(api_type=[ApiType.REST]))) == ""
    assert extract_database_name(None) == ""


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
        "src.common.utils.session_info_metadata.load_session_metadata",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        rest_target = await get_session_connection_target(session_id, protocol=ApiType.REST)
        scim_target = await get_session_connection_target(session_id, protocol=ApiType.SCIM)
        sql_target = await get_session_connection_target(session_id, protocol=ApiType.SQL)

    assert rest_target == ("https://h/api/v2/", "")
    assert scim_target == ("https://h/scim/v2/", "")
    assert sql_target == ("", "hr_db")
