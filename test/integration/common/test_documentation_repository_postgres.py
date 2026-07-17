# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.common.database.models import Base, DocumentationItem, Session
from src.common.database.repositories.documentation_repository import DocumentationRepository


@pytest.mark.asyncio
async def test_get_conndev_documentation_items_matches_python_content_type_normalization():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL repository integration tests")

    schema_name = f"test_conndev_repository_{uuid4().hex}"
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

        session_id = uuid4()
        other_session_id = uuid4()
        first_created_at = datetime.now(timezone.utc)
        second_created_at = first_created_at + timedelta(seconds=1)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with session_factory() as db:
            db.add_all(
                [
                    Session(session_id=session_id),
                    Session(session_id=other_session_id),
                    DocumentationItem(
                        session_id=session_id,
                        source="upload",
                        content="first",
                        doc_metadata={"content_type": " APPLICATION/CONNDEV+JSON; charset=utf-8 "},
                        created_at=first_created_at,
                    ),
                    DocumentationItem(
                        session_id=session_id,
                        source="upload",
                        content="second",
                        doc_metadata={"content_type": "application/com.evolveum.conndev+json"},
                        created_at=second_created_at,
                    ),
                    DocumentationItem(
                        session_id=session_id,
                        source="upload",
                        content="ordinary-json",
                        doc_metadata={"content_type": "application/json"},
                    ),
                    DocumentationItem(
                        session_id=session_id,
                        source="upload",
                        content="missing-content-type",
                        doc_metadata={},
                    ),
                    DocumentationItem(
                        session_id=other_session_id,
                        source="upload",
                        content="other-session",
                        doc_metadata={"content_type": "application/conndev+json"},
                    ),
                ]
            )
            await db.commit()

            items = await DocumentationRepository(db).get_conndev_documentation_items_by_session(session_id)

        assert [item["content"] for item in items] == ["first", "second"]
    finally:
        if schema_created:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
