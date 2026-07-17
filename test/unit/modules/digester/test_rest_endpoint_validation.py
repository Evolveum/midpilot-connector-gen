# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.digester.extractors.rest.endpoints import _apply_validated_endpoint_details
from src.modules.digester.prompts.rest.endpoints_prompts import (
    check_endpoint_params_system_prompt,
    check_endpoint_params_user_prompt,
    get_endpoints_system_prompt,
    get_endpoints_user_prompt,
)
from src.modules.digester.schemas import EndpointParamInfo, ExtractedEndpointInfo


def test_rest_endpoint_extraction_models_exclude_structured_parameters():
    """Structured parameters are SCIM-only; REST LLM extraction must not request them."""
    assert "parameters" not in ExtractedEndpointInfo.model_fields
    assert "parameters" not in EndpointParamInfo.model_fields


def test_rest_endpoint_prompts_do_not_request_structured_parameters():
    for prompt in (
        get_endpoints_system_prompt,
        get_endpoints_user_prompt,
        check_endpoint_params_system_prompt,
        check_endpoint_params_user_prompt,
    ):
        assert "path, query, and header parameters" not in prompt
        assert "structured request parameters" not in prompt


def test_apply_validated_endpoint_details_copies_editable_fields():
    endpoint = ExtractedEndpointInfo(
        path="/users/{id}",
        method="GET",
        description="Get a user",
    )
    checked_result = EndpointParamInfo(
        description="Get a user by identifier",
        response_content_type="application/json",
        suggested_use=["getById"],
    )

    _apply_validated_endpoint_details(endpoint, checked_result)

    assert endpoint.description == "Get a user by identifier"
    assert endpoint.response_content_type == "application/json"
    assert endpoint.suggested_use == ["getById"]
    assert endpoint.path == "/users/{id}"
    assert endpoint.method == "GET"
