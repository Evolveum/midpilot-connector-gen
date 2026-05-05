# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.modules.codegen.schema import CodegenOperationInput, GroovyCodePayload, PreferredEndpointsInput
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
