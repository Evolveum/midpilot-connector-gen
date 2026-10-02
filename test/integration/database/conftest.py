# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared PostgreSQL fixtures for the database integration tests.

Every test runs against a throwaway schema of the configured test database, so tests never
observe each other's rows and a failed run leaves nothing behind.
"""

import os
import re
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models import Base

SessionFactory = async_sessionmaker[AsyncSession]

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"
_SKIP_REASON = f"{TEST_DATABASE_URL_ENV} is required for PostgreSQL repository integration tests"


def _schema_name(request: pytest.FixtureRequest) -> str:
    """Name the throwaway schema after the requesting test, so a leftover is traceable.

    PostgreSQL truncates identifiers at 63 characters, so both halves are bounded rather
    than letting a long test name silently collide after truncation.
    """
    stem = re.sub(r"[^a-z0-9]+", "_", request.node.name.lower())[:24].strip("_")
    return f"test_{stem}_{uuid4().hex[:16]}"


@pytest_asyncio.fixture
async def postgres_session_factory(request: pytest.FixtureRequest) -> AsyncIterator[SessionFactory]:
    """Yield a session factory bound to a dedicated schema, dropped when the test ends."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(_SKIP_REASON)

    schema_name = _schema_name(request)
    engine = create_async_engine(
        database_url,
        execution_options={"schema_translate_map": {None: schema_name}},
    )
    schema_created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
            await connection.run_sync(Base.metadata.create_all)

        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
