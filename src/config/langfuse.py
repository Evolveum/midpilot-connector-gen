# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional

from pydantic import BaseModel


class LangfuseSettings(BaseModel):
    """
    Configuration for the Langfuse client.

    :param public_key: Public Langfuse host key.
    :param secret_key: Secret Langfuse host key.
    :param host: Langfuse host.
    :param ca_cert_file: Optional CA certificate file for internal Langfuse TLS.
    :param tracing_enabled: Enable/disable langfuse tracing.
    :param environment: Environment name e.g. demo, dev-myname.
    """

    public_key: str = "emptykey"
    secret_key: str = "emptykey"
    host: str = ""
    ca_cert_file: Optional[str] = None
    tracing_enabled: bool = False
    environment: str = "dev-whoami"
