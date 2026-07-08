# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

ReasoningEffort = Literal["low", "medium", "high"]


class LLMSettings(BaseModel):
    """
    Configuration for the LLM client.

    :param openai_api_key: API key for OpenAI-compatible services.
    :param openai_api_base: Base URL for the API endpoint.
    :param model_name: Default model identifier to use.
    :param request_timeout: Timeout for API requests in seconds.
    :param ca_cert_file: Optional CA certificate file for internal TLS.
    :param max_connections: Max total connections in the shared LLM HTTP pool.
    :param max_keepalive_connections: Max idle keep-alive connections retained in the pool.
    """

    openai_api_key: str = ""
    openai_api_base: str = "https://openrouter.ai/api/v1"
    model_name: str = "openai/gpt-oss-120b"
    request_timeout: int = 600
    provider_order: List[str] = Field(
        ["groq", "wandb/fp4", "clarifai/fp4"],
        description="List of LLM providers in order of preference",
    )
    reasoning_effort: ReasoningEffort | None = Field(
        None,
        description="Optional reasoning effort for models that support it.",
    )
    ca_cert_file: Optional[str] = None
    max_connections: int = Field(
        100,
        description="Maximum total connections in the shared LLM HTTP connection pool.",
    )
    max_keepalive_connections: int = Field(
        30,
        description="Maximum idle keep-alive connections retained in the shared LLM HTTP pool.",
    )
