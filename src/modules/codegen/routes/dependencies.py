# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""HTTP request validation shared by connector-code override routes."""

import asyncio
from typing import Annotated

from fastapi import Body
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from src.modules.codegen.schema import ConnectorCodeInput, GroovyCodePayload


async def validated_connector_code(
    payload: Annotated[ConnectorCodeInput, Body(description="Connector code as JSON")],
) -> GroovyCodePayload:
    """Parse YAML/Groovy in a worker while retaining FastAPI's validation errors."""
    body = payload.model_dump()
    try:
        return await asyncio.to_thread(GroovyCodePayload.model_validate, body)
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        raise RequestValidationError(errors, body=body) from exc
