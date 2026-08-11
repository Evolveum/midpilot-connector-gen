# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.shared.auth import AuthType, auth_entity_key, auth_match_key, normalize_auth_type


def test_auth_identity_normalizes_name_formatting_and_type_aliases():
    expected_identity = ("bearertoken", "jwtbearer")

    assert auth_match_key("Bearer Token", "jwt-bearer") == expected_identity
    assert auth_match_key("bearer-token", AuthType.JWT_BEARER) == expected_identity
    assert auth_entity_key("Bearer Token", "jwt-bearer") == "bearertoken|jwtbearer"


def test_auth_type_normalization_uses_the_shared_vocabulary():
    assert normalize_auth_type("x-api-key") is AuthType.API_KEY
    assert normalize_auth_type("client_credentials") is AuthType.OAUTH2_CLIENT_CREDENTIALS
