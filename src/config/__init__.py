# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config.app import AppSettings
from src.config.auth import AuthSettings
from src.config.database import DatabaseSettings
from src.config.digester import DigesterSettings
from src.config.langfuse import LangfuseSettings
from src.config.llm import LLMSettings, ReasoningEffort
from src.config.logging import LoggingSettings, LogLevel
from src.config.scrape import ScrapeAndProcessSettings
from src.config.search import BraveSettings, SearchSettings


class Settings(BaseSettings):
    """
    Application settings loaded from environment or defaults.

    Uses nested environment variables with '__' delimiter.

    Example: LOGGING__LEVEL=error
             DATABASE__HOST=localhost
             DATABASE__EXT_PORT=5433
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    auth: AuthSettings = AuthSettings()
    logging: LoggingSettings = LoggingSettings()
    llm: LLMSettings = LLMSettings()
    langfuse: LangfuseSettings = LangfuseSettings()
    search: SearchSettings = SearchSettings()
    scrape_and_process: ScrapeAndProcessSettings = ScrapeAndProcessSettings()
    digester: DigesterSettings = DigesterSettings()
    brave: BraveSettings = BraveSettings()
    database: DatabaseSettings = DatabaseSettings()


config = Settings()

__all__ = [
    "AppSettings",
    "AuthSettings",
    "BraveSettings",
    "DatabaseSettings",
    "DigesterSettings",
    "LLMSettings",
    "LangfuseSettings",
    "LogLevel",
    "LoggingSettings",
    "ReasoningEffort",
    "ScrapeAndProcessSettings",
    "SearchSettings",
    "Settings",
    "config",
]
