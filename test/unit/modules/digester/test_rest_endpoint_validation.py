# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from uuid import uuid4

import pytest

from src.modules.digester.aggregation.merges import merge_endpoint_candidates
from src.modules.digester.extractors.rest.endpoints import _apply_validated_endpoint_details
from src.modules.digester.prompts.rest.endpoints_prompts import (
    check_endpoint_params_system_prompt,
    get_endpoints_system_prompt,
)
from src.modules.digester.schemas import EndpointParamInfo, EndpointRequestParameter, ExtractedEndpointInfo


def test_rest_endpoint_prompts_explicitly_request_structured_parameters():
    assert "path, query, and header parameters" in get_endpoints_system_prompt
    assert "Every path placeholder" in check_endpoint_params_system_prompt
    assert "Do not convert request-body properties" in check_endpoint_params_system_prompt


@pytest.mark.asyncio
async def test_validated_rest_endpoint_parameters_remain_typed_during_merge():
    endpoint = ExtractedEndpointInfo(
        path="/users/{id}",
        method="GET",
        description="Get a user",
    )
    checked_result = EndpointParamInfo(
        description="Get a user by identifier",
        parameters=[
            EndpointRequestParameter(
                name="id",
                location="path",
                type="string",
                required=True,
            )
        ],
    )

    _apply_validated_endpoint_details(endpoint, checked_result)

    assert isinstance(endpoint.parameters[0], EndpointRequestParameter)

    merged = await merge_endpoint_candidates([endpoint], "user", uuid4())

    assert merged[0]["parameters"] == [
        {
            "name": "id",
            "location": "path",
            "type": "string",
            "description": "",
            "required": True,
            "minimum": None,
            "maximum": None,
            "allowedValues": [],
        }
    ]
