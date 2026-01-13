#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import json
from typing import Callable

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from ..config import config

"""Langfuse integration functions, used for development and testing purposes"""

# https://langfuse.com/docs/observability/sdk/python/setup
langfuse = Langfuse(
    host=config.langfuse.host,
    public_key=config.langfuse.public_key,
    secret_key=config.langfuse.secret_key,
    tracing_enabled=config.langfuse.tracing_enabled,
    environment=config.langfuse.environment,
)

# langfuse langchain handler that automatically observes runnables (chains)
langfuse_handler = CallbackHandler(public_key=config.langfuse.public_key)


class ObservedRoute(APIRoute):
    """
    Custom API route that starts new langfuse trace and automatically observes request and response.
    By default, only non-GET requests are traced to avoid noise from health/docs/static endpoints.
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            # Skip observability for GET endpoints (e.g., status polling)
            if request.method.upper() == "GET":
                return await original_route_handler(request)

            # Attempt to read request body as JSON only when appropriate
            request_json = None
            if request.headers.get("content-type", "").lower().startswith("application/json"):
                try:
                    request_json = await request.json()
                except Exception:
                    # Body is not valid JSON; leave as None to avoid crashing
                    request_json = None

            with langfuse.start_as_current_span(name="api_request", input=request_json) as span:
                span.update_trace(name=request.url.path, tags=["wp1"])

                # Execute the actual route handler
                response: Response = await original_route_handler(request)

                # Safely transform response body for observability
                response_body = getattr(response, "body", b"")
                response_json = None
                if response_body:
                    try:
                        response_json = json.loads(response_body)
                    except Exception:
                        # Store raw (decoded) body if it is not valid JSON
                        response_json = (
                            response_body.decode(errors="ignore")
                            if isinstance(response_body, (bytes, bytearray))
                            else str(response_body)
                        )

                span.update(output=response_json)
                return response

        return custom_route_handler


def ObservableAPIRouter():
    """
    Custom API router that automtatically start observing every route with langfuse.
    """

    return APIRouter(route_class=ObservedRoute)
