# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class DatabaseSettings(BaseModel):
    """
    Configuration for PostgreSQL database connection.

    :param url: Full database connection URL (used by SQLAlchemy)
    :param host: Database host address
    :param int_port: Internal database port used inside Docker/networked deployments
    :param ext_port: External database port exposed to the host
    :param name: Database name
    :param user: Database username
    :param password: Database password
    :param pool_size: Connection pool size
    :param max_overflow: Maximum overflow connections
    :param echo: Enable SQL query logging (for debugging)
    """

    url: Optional[str] = Field(
        default=None,
        description="Database URL",
    )
    host: str = ""
    int_port: int = 5432
    ext_port: int = 5433
    name: str = ""
    user: str = ""
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False

    @model_validator(mode="after")
    def assemble_db_url(self) -> "DatabaseSettings":
        """Construct the database URL from components if not provided or contains placeholders."""
        if not self.url or "${" in self.url:
            self.url = f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.int_port}/{self.name}"
        return self
