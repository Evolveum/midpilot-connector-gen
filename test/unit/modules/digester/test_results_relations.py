# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.modules.digester.results import store_relations_override


@pytest.mark.asyncio
async def test_manual_relation_override_clears_machine_analysis_in_the_same_write() -> None:
    repo = MagicMock()
    repo.update_session = AsyncMock(return_value=True)
    session_id = uuid4()
    payload: dict[str, list[object]] = {"relations": []}

    await store_relations_override(repo, session_id, payload)

    repo.update_session.assert_awaited_once_with(
        session_id,
        {
            "relationsOutput": payload,
            "relationsAnalysisOutput": None,
        },
    )
