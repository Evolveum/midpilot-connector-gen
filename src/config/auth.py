# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import Enum

from pydantic import BaseModel


class AuthMode(str, Enum):
    prod = "prod"
    dev = "dev"


class AuthSettings(BaseModel):
    """Gateway authentication boundary; development explicitly bypasses ownership.

    Production mode requires a key already validated by the gateway. Deployment
    must prevent callers from reaching the backend without passing that gateway.
    """

    mode: AuthMode = AuthMode.prod
