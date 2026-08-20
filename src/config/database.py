# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel, Field, SecretStr, model_validator


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

    url: Optional[SecretStr] = Field(
        default=None,
        description="Database URL. Secret because it embeds the password.",
    )
    host: str = ""
    int_port: int = 5432
    ext_port: int = 5433
    name: str = ""
    user: str = ""
    password: SecretStr = SecretStr("")
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False

    @model_validator(mode="after")
    def assemble_db_url(self) -> "DatabaseSettings":
        """Construct the database URL from components if not provided or contains placeholders."""
        configured_url = self.url.get_secret_value() if self.url else ""
        if not configured_url or "${" in configured_url:
            self.url = SecretStr(
                f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
                f"@{self.host}:{self.int_port}/{self.name}"
            )
        return self

    def database_url(self) -> str:
        """Return the connection URL in plaintext, for handing to the driver.

        The only place the URL is unwrapped. Keeping it behind a method makes
        every plaintext use of the credential greppable.
        """
        return self.url.get_secret_value() if self.url else ""
