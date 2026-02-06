#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, Enum):
    """
    Log level settings for the application.

    :cvar debug: Debug-level logging, most verbose.
    :cvar info: Informational messages, default level.
    :cvar warning: Warning messages, potential issues.
    :cvar error: Error messages, serious problems.
    :cvar critical: Critical errors, application shutdown scenarios.
    """

    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class LoggingSettings(BaseModel):
    """
    Configuration for application logging.

    :param level: LogLevel enum specifying the logging threshold.
    :param access_log: Enable or disable access logs.
    :param colors: Enable or disable colored log output.
    :param live_reload: Enable live reloading of logs on code changes.
    """

    level: LogLevel = LogLevel.info
    access_log: bool = True
    colors: bool = False
    live_reload: bool = False


class LLMSettings(BaseModel):
    """
    Configuration for the LLM client.

    :param openai_api_key: API key for OpenAI-compatible services.
    :param openai_api_base: Base URL for the API endpoint.
    :param model_name: Default model identifier to use.
    :param request_timeout: Timeout for API requests in seconds.
    """

    openai_api_key: str = ""
    openai_api_base: str = "https://openrouter.ai/api/v1"
    model_name: str = "openai/gpt-oss-20b"
    request_timeout: int = 120


class LLMSmall1Settings(BaseModel):
    """
    Configuration for the LLM client.

    :param openai_api_key: API key for OpenAI-compatible services.
    :param openai_api_base: Base URL for the API endpoint.
    :param model_name: Default model identifier to use.
    :param request_timeout: Timeout for API requests in seconds.
    """

    openai_api_key: str = ""
    openai_api_base: str = "https://openrouter.ai/api/v1"
    model_name: str = "openai/gpt-oss-20b"
    request_timeout: int = 120


class LLMSmall2Settings(BaseModel):
    """
    Configuration for the LLM client.

    :param openai_api_key: API key for OpenAI-compatible services.
    :param openai_api_base: Base URL for the API endpoint.
    :param model_name: Default model identifier to use.
    :param request_timeout: Timeout for API requests in seconds.
    """

    openai_api_key: str = ""
    openai_api_base: str = "https://openrouter.ai/api/v1"
    model_name: str = "openai/gpt-oss-20b"
    request_timeout: int = 120


# We dont use embedding model
# class EmbeddingsSettings(BaseModel):
#     """
#     Configuration for the Embeddings client.
#
#     :param openai_api_key: API key for OpenAI-compatible services.
#     :param openai_api_base: Base URL for the API endpoint.
#     :param model_name: Default model identifier to use.
#     :param request_timeout: Timeout for API requests in seconds.
#     """
#
#     openai_api_key: str = ""
#     openai_api_base: str = "http://localhost:11434"
#     model_name: str = "nomic-ai/nomic-embed-text-v1.5"


class LangfuseSettings(BaseModel):
    """
    Configuration for the Langfuse client.

    :param public_key: Public Langfuse host key.
    :param secret_key: Secret Langfuse host key.
    :param host: Langfuse host.
    :param tracing_enabled: Enable/disable langfuse tracing.
    :param environment: Environment name e.g. demo, dev-myname.
    """

    public_key: str = "emptykey"
    secret_key: str = "emptykey"
    host: str = ""
    tracing_enabled: bool = False
    environment: str = "dev-whoami"


class SearchSettings(BaseModel):
    """Search method specification of Discovery module."""

    method_name: str = ""


class BraveSettings(BaseModel):
    """Configuration for Brave Search API."""

    api_key: str = ""
    endpoint: str = "https://api.search.brave.com/res/v1/web/search"


class ScrapeAndProcessSettings(BaseModel):
    """
    Configuration for Scrape and Process module.
    """

    # Scraper controls
    max_scraper_iterations: int = Field(
        4,
        description="Max outer iterations of the scraper loop",
    )
    max_iterations_filter_irrelevant: int = Field(
        5,
        description="Max LLM filtering passes per iteration",
    )
    forbidden_url_parts: list[str] = Field(
        [
            "logout",
            "login",
            "signup",
            "register",
            "subscribe",
            "pricing",
            "plans",
            "terms",
            "privacy",
            "contact",
            "about",
            "blog",
            "news",
            "forum",
            "release-notes",
            "changelog",
            "es",
            "pt",
            "de",
            "fr",
            "jp",
            "zh",
            "sk",
            "ru",
            "fr",
            "it",
            "nl",
            "pl",
            "tr",
        ],
        description="URL substrings to consider irrelevant while scraping",
    )

    # Chunking controls
    chunk_length: int = Field(
        20000,
        description="Max tokens per chunk for LLM processing",
    )
    max_concurrent: int = Field(
        20,
        description="Max concurrent chunk processing tasks",
    )

    chunk_categories: list[str] = Field(
        [
            "spec_yaml",
            "spec_json",
            "reference_api",
            "reference_other",
            "overview",
            "index",
            "tutorial",
            "non-technical",
            "other",
        ],
        description="List of chunk categories to consider while processing, to be used in Literal type",
    )

    latest_version_synonyms: list[str] = Field(
        [
            "latest",
            "current",
            "newest",
            "development",
            "stable",
            "up-to-date",
        ],
        description="List of synonyms indicating latest version in documentation",
    )

    unknown_version_threshold: float = Field(
        0.9,
        description="If the number of chunks with unknown version exceeds this ratio, the app version is considered unknown",
    )

    metadata_uncertainty_threshold: float = Field(
        0.05,
        description="If any parameter in metadata is present in less than this ratio of chunks, it is considered uncertain and ignored",
    )


class DatabaseSettings(BaseModel):
    """
    Configuration for PostgreSQL database connection.

    :param url: Full database connection URL (used by SQLAlchemy)
    :param host: Database host address
    :param port: Database port
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
    port: int = 5432
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
            self.url = f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        return self


class AppSettings(BaseModel):
    """
    Core application settings for the API service.

    :param title: API title shown in docs.
    :param version: API version string.
    :param description: API description displayed in docs.
    :param api_base_url: Base path for all routes.
    :param host: Host address for Uvicorn server.
    :param port: Port number for Uvicorn server.
    :param live_reload: Enable Uvicorn live reload on changes.
    :param workers: Number of worker processes.
    :param proxy_headers: Trust proxy headers.
    :param forwarded_allow_ips: IPs allowed to be forwarded.
    :param root_path: Root path for mounting.
    :param timeout_keep_alive: Keep-alive timeout for connections.
    :param timeout_graceful_shutdown: Graceful shutdown timeout.
    :param limit_concurrency: Optional limit on concurrent requests.
    :param limit_max_requests: Optional max requests per worker.
    :param ssl_certfile: Optional path to SSL certificate file.
    :param ssl_keyfile: Optional path to SSL key file.
    """

    title: str = "Smart Integration Microservice"
    version: str = "0.1.0"
    description: str = "Smart Integration Microservice for scraping, digester and CodeGen"
    api_base_url: str = "/api"

    host: str = "0.0.0.0"
    port: int = 8090
    live_reload: bool = False
    workers: int = 1
    proxy_headers: bool = True
    forwarded_allow_ips: str = "*"
    root_path: str = ""
    timeout_keep_alive: int = 10
    timeout_graceful_shutdown: int = 15
    limit_concurrency: Optional[int] = None
    limit_max_requests: Optional[int] = None
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None


class Settings(BaseSettings):
    """
    Application settings loaded from environment or defaults.

    Uses nested environment variables with '__' delimiter.

    Example: LOGGING__LEVEL=error
             DATABASE__HOST=localhost
             DATABASE__PORT=5432
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    logging: LoggingSettings = LoggingSettings()
    llm: LLMSettings = LLMSettings()
    llm_small1: LLMSmall1Settings = LLMSmall1Settings()
    llm_small2: LLMSmall2Settings = LLMSmall2Settings()
    # embeddings: EmbeddingsSettings = EmbeddingsSettings()
    langfuse: LangfuseSettings = LangfuseSettings()
    search: SearchSettings = SearchSettings()
    scrape_and_process: ScrapeAndProcessSettings = ScrapeAndProcessSettings()
    brave: BraveSettings = BraveSettings()
    database: DatabaseSettings = DatabaseSettings()


config = Settings()
