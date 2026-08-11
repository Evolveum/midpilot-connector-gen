# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from enum import StrEnum


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RelevantLevel(StrEnum):
    TRUE = "true"
    FALSE = "false"
    MAYBE = "maybe"


class EndpointType(StrEnum):
    CONSTANT = "constant"
    DYNAMIC = "dynamic"
    UNKNOWN = ""


class EndpointMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
