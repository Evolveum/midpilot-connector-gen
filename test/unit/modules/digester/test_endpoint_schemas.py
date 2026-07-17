# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.digester.enums import EndpointMethod
from src.modules.digester.schemas import (
    EndpointResponse,
    ExtractedEndpointInfo,
    ExtractedEndpointResponse,
    RankedEndpointKey,
)


def test_extraction_schema_excludes_system_populated_fields():
    """The LLM structured-output schema must not expose system-populated fields."""
    endpoint_schema = ExtractedEndpointResponse.model_json_schema()["$defs"]["ExtractedEndpointInfo"]
    assert "parameters" not in endpoint_schema["properties"]
    assert "relevantDocumentations" not in endpoint_schema["properties"]


def test_endpoint_method_is_normalized_before_validation():
    endpoint = ExtractedEndpointInfo(path="/Users", method=" get ", description="List users")
    assert endpoint.method == EndpointMethod.GET

    ranked = RankedEndpointKey.model_validate({"method": "delete", "path": "/Users/{id}"})
    assert ranked.method == EndpointMethod.DELETE


def test_endpoint_response_round_trips_scim_pregenerated_parameters():
    """SCIM pregenerated endpoints keep their structured parameters through the final model."""
    payload = {
        "endpoints": [
            {
                "path": "/Users",
                "method": "GET",
                "description": "Retrieve all Users",
                "responseContentType": "application/scim+json",
                "requestContentType": None,
                "suggestedUse": ["getAll", "search"],
                "parameters": [
                    {
                        "name": "sortOrder",
                        "location": "query",
                        "type": "string",
                        "required": False,
                        "allowedValues": ["ascending", "descending"],
                    }
                ],
                "relevantDocumentations": [],
            }
        ],
        "scimCapabilities": None,
    }

    response = EndpointResponse.model_validate(payload)
    assert response.endpoints[0].parameters[0].allowed_values == ["ascending", "descending"]

    dumped = response.model_dump(by_alias=True)
    assert "scimCapabilities" in dumped
    assert dumped["endpoints"][0]["parameters"][0]["allowedValues"] == ["ascending", "descending"]
    assert dumped["endpoints"][0]["suggestedUse"] == ["getAll", "search"]
