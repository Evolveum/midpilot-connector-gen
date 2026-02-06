# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import Enum
from typing import Iterable


class ApiProtocol(str, Enum):
    REST = "rest"
    SCIM = "scim"


def detect_protocol(api_types: Iterable[str]) -> ApiProtocol:
    api_types_set = {str(x).upper() for x in api_types}
    return ApiProtocol.SCIM if "SCIM" in api_types_set else ApiProtocol.REST
