# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.modules.codegen.schema import GroovyCodePayload
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
