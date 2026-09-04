# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from src.config import config

# Get database settings from main config
db_config = config.database

# Validate database URL
database_url = db_config.database_url()
if not database_url:
    raise ValueError("DATABASE__URL must be configured in environment or .env file")

# Create async engine using settings from main config
engine = create_async_engine(
    database_url,
    echo=db_config.echo,
    pool_size=db_config.pool_size,
    max_overflow=db_config.max_overflow,
    pool_pre_ping=True,
)

# Create session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Base class for SQLAlchemy models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session and commit it when the caller returns.

    API code must not depend on this directly; use :data:`DbSession` as the default for a
    route's ``db`` parameter. That alias pins ``scope="function"`` so the commit here runs
    before the response is serialized and sent, and only a successful commit lets the response
    through. Exceptions from the handler, response validation or the commit roll the
    transaction back before error handling.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


DbSession = Depends(get_db, scope="function")
"""Shared FastAPI dependency for the request-scoped database session."""


async def close_db() -> None:
    """
    Close database connections.
    Should be called on application shutdown.
    """
    await engine.dispose()
