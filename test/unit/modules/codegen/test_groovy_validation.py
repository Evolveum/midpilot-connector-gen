# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.modules.codegen.schema import (
    AuthorizationCodegenInput,
    CodegenOperationInput,
    GroovyCodePayload,
    PreferredEndpointsInput,
)
from src.modules.codegen.utils.groovy_validation import GroovyValidationError, validate_groovy_code


def test_validate_groovy_code_prefers_real_backend_result() -> None:
    with patch(
        "src.modules.codegen.utils.groovy_validation._parse_groovy_code",
        return_value=None,
    ):
        result = validate_groovy_code('objectClass("User") {}')

    assert result is None


def test_validate_groovy_code_reports_backend_unavailable() -> None:
    with patch(
        "src.modules.codegen.utils.groovy_validation._parse_groovy_code",
        side_effect=ImportError,
    ):
        result = validate_groovy_code('objectClass("User") {}')

    assert result == "Groovy validation backend is unavailable. Install `groovy-parser`."


def test_groovy_code_payload_raises_when_validation_fails() -> None:
    failure = GroovyValidationError("syntax error")

    with patch("src.modules.codegen.schema.ensure_valid_groovy_code", side_effect=failure):
        with pytest.raises(ValidationError):
            GroovyCodePayload.model_validate({"code": 'objectClass("User") {'})


def test_preferred_endpoints_input_accepts_preferred_payload() -> None:
    wrapped_preferred = PreferredEndpointsInput.model_validate(
        {
            "preferredEndpoints": [
                {"method": "POST", "path": "/users"},
                {"method": "GET", "path": "/users/{id}"},
            ]
        }
    )

    expected = [
        {"method": "POST", "path": "/users"},
        {"method": "GET", "path": "/users/{id}"},
    ]
    assert [endpoint.model_dump() for endpoint in wrapped_preferred.preferred_endpoints] == expected


def test_authorization_input_requires_preferred_authorizations() -> None:
    with pytest.raises(ValidationError):
        AuthorizationCodegenInput.model_validate({})

    with pytest.raises(ValidationError):
        AuthorizationCodegenInput.model_validate({"preferredAuthorizations": []})


def test_authorization_input_requires_name_and_type() -> None:
    with pytest.raises(ValidationError):
        AuthorizationCodegenInput.model_validate({"preferredAuthorizations": [{"name": "Bearer token"}]})

    with pytest.raises(ValidationError):
        AuthorizationCodegenInput.model_validate({"preferredAuthorizations": [{"type": "bearer"}]})

    with pytest.raises(ValidationError):
        AuthorizationCodegenInput.model_validate({"preferredAuthorizations": [{"name": "Bearer token", "type": ""}]})


def test_authorization_input_normalizes_known_auth_type_aliases() -> None:
    wrapped_preferred = AuthorizationCodegenInput.model_validate(
        {
            "preferredAuthorizations": [
                {"name": "HTTP JWT Bearer Token Authorization", "type": "jwt-bearer"},
                {"name": "OAuth2 JWT Bearer", "type": "oauth2-jwt"},
                {"name": "Custom auth", "type": "custom-experimental"},
            ]
        }
    )

    assert [authorization.type for authorization in wrapped_preferred.preferred_authorizations] == [
        "jwtBearer",
        "oauth2Jwt",
        "custom-experimental",
    ]


def test_authorization_input_accepts_empty_repair_context_for_other_authorization() -> None:
    operation_input = AuthorizationCodegenInput.model_validate(
        {
            "currentScript": "",
            "midpointErrors": [""],
            "preferredAuthorizations": [
                {
                    "name": "other",
                    "type": "other",
                    "quirks": "",
                }
            ],
        }
    )

    assert not operation_input.is_repair
    assert operation_input.context_payload() == {"preferredAuthorizations": [{"name": "other", "type": "other"}]}


def test_codegen_operation_input_derives_repair_mode_without_validating_current_script() -> None:
    operation_input = CodegenOperationInput.model_validate(
        {
            "currentScript": 'objectClass("User") {',
            "midpointErrors": ["Missing method: request.pathParameter(...)"],
        }
    )

    assert operation_input.is_repair
    assert operation_input.repair_context() is not None
    assert operation_input.context_payload() == {
        "currentScript": 'objectClass("User") {',
        "midpointErrors": ["Missing method: request.pathParameter(...)"],
    }
