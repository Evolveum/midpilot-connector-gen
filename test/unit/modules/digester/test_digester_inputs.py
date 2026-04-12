# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.modules.digester.utils.criteria import AUTH_CRITERIA, DEFAULT_CRITERIA
from src.modules.digester.utils.inputs import auth_input


@pytest.mark.asyncio
async def test_auth_input_uses_auth_criteria_when_matches_docs():
    session_id = uuid4()
    db = MagicMock()
    auth_docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "auth chunk"}]

    with patch("src.modules.digester.utils.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter:
        mock_filter.return_value = auth_docs
        result = await auth_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == auth_docs
    assert result["jobInput"]["usedAuthCriteria"] is True
    assert result["args"] == (auth_docs, True, session_id)
    mock_filter.assert_awaited_once_with(AUTH_CRITERIA, session_id, db=db)


@pytest.mark.asyncio
async def test_auth_input_falls_back_to_default_when_auth_filter_empty():
    session_id = uuid4()
    db = MagicMock()
    default_docs = [{"chunkId": str(uuid4()), "docId": str(uuid4()), "content": "default chunk"}]

    with patch("src.modules.digester.utils.inputs.filter_documentation_items", new_callable=AsyncMock) as mock_filter:
        mock_filter.side_effect = [[], default_docs]
        result = await auth_input(db=db, session_id=session_id)

    assert result["jobInput"]["documentationItems"] == default_docs
    assert result["jobInput"]["usedAuthCriteria"] is False
    assert result["args"] == (default_docs, False, session_id)
    mock_filter.assert_has_awaits(
        [
            call(AUTH_CRITERIA, session_id, db=db),
            call(DEFAULT_CRITERIA, session_id, db=db),
        ]
    )
