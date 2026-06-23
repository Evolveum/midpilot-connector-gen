# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.config import config
from src.modules.digester.inputs import auth_input, connectivity_endpoint_input, metadata_input
from src.modules.digester.selection.criteria import (
    CONNECTIVITY_ENDPOINT_CRITERIA,
    CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA,
    DEFAULT_AUTH_CRITERIA,
    EXTENDED_AUTH_CRITERIA,
    METADATA_CRITERIA,
)


@pytest.mark.asyncio
async def test_auth_input_uses_auth_criteria_when_matches_docs():
    session_id = uuid4()
    db = MagicMock()
    # Create enough items to pass the minimum threshold (default is 15)
    auth_docs = [
        {"chunkId": str(uuid4()), "docId": str(uuid4()), "content": f"auth chunk {i}"}
        for i in range(config.digester.auth_min_documentation_items + 5)
    ]

    with patch("src.modules.digester.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter:
        mock_filter.return_value = auth_docs
        result = await auth_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == auth_docs
    assert result["jobInput"]["usedAuthCriteria"] is True
    assert result["args"] == (auth_docs,)
    mock_filter.assert_awaited_once_with(DEFAULT_AUTH_CRITERIA, session_id, db=db)


@pytest.mark.asyncio
async def test_auth_input_falls_back_to_extended_when_auth_filter_has_too_few_docs():
    session_id = uuid4()
    db = MagicMock()
    auth_docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "auth chunk"}]
    extended_docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "extended auth chunk"}]

    with patch("src.modules.digester.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter:
        mock_filter.side_effect = [auth_docs, extended_docs]
        result = await auth_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == extended_docs
    assert result["jobInput"]["usedAuthCriteria"] is False
    assert result["args"] == (extended_docs,)
    mock_filter.assert_has_awaits(
        [
            call(DEFAULT_AUTH_CRITERIA, session_id, db=db),
            call(EXTENDED_AUTH_CRITERIA, session_id, db=db),
        ]
    )


@pytest.mark.asyncio
async def test_metadata_input_uses_metadata_criteria_and_includes_application_name():
    session_id = uuid4()
    db = MagicMock()
    docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "metadata chunk"}]

    with (
        patch("src.modules.digester.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter,
        patch("src.modules.digester.inputs.get_discovery_application_name", new_callable=AsyncMock) as mock_app_name,
    ):
        mock_filter.return_value = docs
        mock_app_name.return_value = "Okta"
        result = await metadata_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == docs
    # applicationName must be part of jobInput so it flows into the cache key.
    assert result["jobInput"]["applicationName"] == "Okta"
    assert result["args"] == (docs,)
    mock_filter.assert_awaited_once_with(METADATA_CRITERIA, session_id, db=db)
    mock_app_name.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_metadata_criteria_overrides_categories_with_protocol_tags():
    assert METADATA_CRITERIA.category_override_tags == ["scim", "rest", "sql", "db", "provisioning"]


@pytest.mark.asyncio
async def test_connectivity_endpoint_input_falls_back_when_primary_filter_has_no_docs():
    session_id = uuid4()
    db = MagicMock()
    fallback_docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "GET /status"}]

    with patch("src.modules.digester.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter:
        mock_filter.side_effect = [[], fallback_docs]
        result = await connectivity_endpoint_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == fallback_docs
    assert result["jobInput"]["usedConnectivityEndpointCriteria"] is False
    assert result["args"] == (fallback_docs,)
    mock_filter.assert_has_awaits(
        [
            call(CONNECTIVITY_ENDPOINT_CRITERIA, session_id, db=db),
            call(CONNECTIVITY_ENDPOINT_FALLBACK_CRITERIA, session_id, db=db),
        ]
    )
