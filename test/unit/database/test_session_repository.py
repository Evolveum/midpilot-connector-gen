# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.database.repositories.session_repository import SessionRepository


def _repo_returning(value: object) -> tuple[SessionRepository, MagicMock]:
    """Build a repository whose next scalar read resolves to ``value``."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    db.execute = AsyncMock(return_value=result)
    return SessionRepository(db), db


@pytest.mark.asyncio
async def test_single_key_read_loads_one_row_instead_of_the_whole_session() -> None:
    """Reading one key must not materialize every stored ``*Output`` payload."""
    repo, db = _repo_returning({"apiType": ["REST"]})
    session_id = uuid4()

    assert await repo.get_session_data(session_id, "metadataOutput") == {"apiType": ["REST"]}

    db.execute.assert_awaited_once()
    sql = " ".join(str(db.execute.await_args.args[0]).split())
    assert "session_data.value" in sql
    assert "session_data.key = " in sql
    # A full-session read selects the whole row set; the single-key path must not.
    assert "sessions.session_id" not in sql


@pytest.mark.asyncio
async def test_nested_key_read_loads_only_the_root_key_of_the_path() -> None:
    repo, db = _repo_returning({"rest": {"baseUrl": "https://api.example.test"}})
    session_id = uuid4()

    value = await repo.get_session_data(session_id, ["metadataOutput", "rest", "baseUrl"])

    assert value == "https://api.example.test"
    db.execute.assert_awaited_once()
    assert "metadataOutput" in db.execute.await_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_nested_key_read_returns_none_when_the_path_hits_a_non_dict() -> None:
    repo, _ = _repo_returning("not-a-dict")

    assert await repo.get_session_data(uuid4(), ["metadataOutput", "rest"]) is None


@pytest.mark.asyncio
async def test_single_element_path_does_not_require_a_dict_value() -> None:
    """A one-element path is a plain key read, matching the pre-refactor behavior."""
    repo, _ = _repo_returning("scalar-value")

    assert await repo.get_session_data(uuid4(), ["metadataOutput"]) == "scalar-value"


@pytest.mark.asyncio
async def test_missing_key_and_missing_session_both_read_as_none() -> None:
    repo, _ = _repo_returning(None)

    assert await repo.get_session_data(uuid4(), "absentOutput") is None
