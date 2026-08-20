# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import os
import ssl

import httpx
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from src.config import config

"""Langfuse integration functions, used for development and testing purposes"""


def _configure_langfuse_otlp_certificate() -> None:
    ca_cert_file = config.langfuse.ca_cert_file
    if not ca_cert_file:
        return

    os.environ.setdefault("OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE", ca_cert_file)


def _build_langfuse_httpx_client() -> httpx.Client | None:
    ca_cert_file = config.langfuse.ca_cert_file
    if not ca_cert_file:
        return None

    return httpx.Client(verify=ssl.create_default_context(cafile=ca_cert_file))


_configure_langfuse_otlp_certificate()
langfuse_httpx_client = _build_langfuse_httpx_client()

# https://langfuse.com/docs/observability/sdk/python/setup
langfuse = Langfuse(
    host=config.langfuse.host,
    public_key=config.langfuse.public_key,
    secret_key=config.langfuse.secret_key.get_secret_value(),
    httpx_client=langfuse_httpx_client,
    tracing_enabled=config.langfuse.tracing_enabled,
    environment=config.langfuse.environment,
)

# langfuse langchain handler that automatically observes runnables (chains)
langfuse_handler = CallbackHandler(public_key=config.langfuse.public_key)
