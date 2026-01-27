# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from ...config import config

# Get database settings from main config
db_config = config.database

# Validate database URL
if not db_config.url:
    raise ValueError("DATABASE_URL must be configured in environment or .env file")

# Create async engine using settings from main config
engine = create_async_engine(
    db_config.url,
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
    Dependency for FastAPI to get database session.

    Usage in FastAPI endpoints:
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            ...
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


async def init_db() -> None:
    """
    Initialize database - create all tables.
    Should be called on application startup.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Close database connections.
    Should be called on application shutdown.
    """
    await engine.dispose()
