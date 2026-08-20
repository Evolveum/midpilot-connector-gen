# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Product-wide authentication vocabulary and identity normalization."""

import re
from enum import StrEnum
from typing import Any

from src.shared.coerce import as_str


class AuthType(StrEnum):
    BASIC = "basic"
    BEARER = "bearer"
    JWT_BEARER = "jwtBearer"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2ClientCredentials"
    OAUTH2_PASSWORD = "oauth2Password"
    OAUTH2_JWT = "oauth2Jwt"
    OAUTH2_SAML = "oauth2Saml"
    API_KEY = "apiKey"
    SESSION = "session"
    DIGEST = "digest"
    HAWK = "hawk"
    AWS_SIGNATURE = "awsSignature"
    MTLS = "mtls"
    NTLM = "ntlm"
    OPENID_CONNECT = "openidConnect"
    OTHER = "other"


def _auth_type_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", as_str(value).strip().lower())


_AUTH_TYPE_ALIASES: dict[str, AuthType] = {
    "basic": AuthType.BASIC,
    "basicauth": AuthType.BASIC,
    "httpbasic": AuthType.BASIC,
    "httpbasicauth": AuthType.BASIC,
    "usernamepassword": AuthType.BASIC,
    "bearer": AuthType.BEARER,
    "bearertoken": AuthType.BEARER,
    "httpbearer": AuthType.BEARER,
    "httpbearertoken": AuthType.BEARER,
    "token": AuthType.BEARER,
    "accesstoken": AuthType.BEARER,
    "personalaccesstoken": AuthType.BEARER,
    "pat": AuthType.BEARER,
    "jwt": AuthType.JWT_BEARER,
    "jsonwebtoken": AuthType.JWT_BEARER,
    "jwtbearer": AuthType.JWT_BEARER,
    "jwtbearertoken": AuthType.JWT_BEARER,
    "httpjwtbearer": AuthType.JWT_BEARER,
    "httpjwtbearertoken": AuthType.JWT_BEARER,
    "clientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "clientcredentialsgrant": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauthclientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauth2clientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauth2clientcredentialsgrant": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauth2password": AuthType.OAUTH2_PASSWORD,
    "oauth2passwordgrant": AuthType.OAUTH2_PASSWORD,
    "resourceownerpassword": AuthType.OAUTH2_PASSWORD,
    "oauth2jwt": AuthType.OAUTH2_JWT,
    "oauth2jwtbearer": AuthType.OAUTH2_JWT,
    "oauth2jwtbearergrant": AuthType.OAUTH2_JWT,
    "jwtbearergrant": AuthType.OAUTH2_JWT,
    "oauth2saml": AuthType.OAUTH2_SAML,
    "oauth2samlbearer": AuthType.OAUTH2_SAML,
    "oauth2samlbearergrant": AuthType.OAUTH2_SAML,
    "oauth": AuthType.OTHER,
    "oauth2": AuthType.OTHER,
    "oauth20": AuthType.OTHER,
    "apikey": AuthType.API_KEY,
    "apikeyauth": AuthType.API_KEY,
    "xapikey": AuthType.API_KEY,
    "apiaccesskey": AuthType.API_KEY,
    "session": AuthType.SESSION,
    "cookie": AuthType.SESSION,
    "cookiesession": AuthType.SESSION,
    "sessioncookie": AuthType.SESSION,
    "digest": AuthType.DIGEST,
    "httpdigest": AuthType.DIGEST,
    "httpdigestauth": AuthType.DIGEST,
    "hawk": AuthType.HAWK,
    "hawkauthentication": AuthType.HAWK,
    "aws": AuthType.AWS_SIGNATURE,
    "awssignature": AuthType.AWS_SIGNATURE,
    "awssignaturev4": AuthType.AWS_SIGNATURE,
    "aws4": AuthType.AWS_SIGNATURE,
    "awsv4": AuthType.AWS_SIGNATURE,
    "awssigv4": AuthType.AWS_SIGNATURE,
    "sigv4": AuthType.AWS_SIGNATURE,
    "aws4hmacsha256": AuthType.AWS_SIGNATURE,
    "awsiam": AuthType.AWS_SIGNATURE,
    "mtls": AuthType.MTLS,
    "mutualtls": AuthType.MTLS,
    "mutualtlsclientauthentication": AuthType.MTLS,
    "clientcertificate": AuthType.MTLS,
    "clientcert": AuthType.MTLS,
    "ntlm": AuthType.NTLM,
    "ntlmauth": AuthType.NTLM,
    "windowsauth": AuthType.NTLM,
    "windowsauthentication": AuthType.NTLM,
    "openidconnect": AuthType.OPENID_CONNECT,
    "openidconnectauth": AuthType.OPENID_CONNECT,
    "oidc": AuthType.OPENID_CONNECT,
    "openid": AuthType.OPENID_CONNECT,
    "other": AuthType.OTHER,
    "custom": AuthType.OTHER,
    "unknown": AuthType.OTHER,
}
_AUTH_TYPE_ALIASES.update({_auth_type_key(auth_type.value): auth_type for auth_type in AuthType})


def is_known_auth_type(value: Any) -> bool:
    """Return whether a value maps to a supported authentication type or alias."""
    return _auth_type_key(value) in _AUTH_TYPE_ALIASES


def normalize_auth_type(value: Any) -> AuthType:
    """Normalize authentication type aliases to the public contract."""
    return _AUTH_TYPE_ALIASES.get(_auth_type_key(value), AuthType.OTHER)


def normalize_auth_type_value(value: Any, *, preserve_unknown: bool = False) -> str | None:
    """Normalize an authentication type to its API value."""
    if value is None:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None
    if preserve_unknown and not is_known_auth_type(raw_value):
        return raw_value
    return normalize_auth_type(raw_value).value


def auth_type_match_key(value: Any) -> str:
    """Build the canonical identity component for an authentication type."""
    if value is None:
        return ""
    if is_known_auth_type(value):
        return normalize_auth_type(value).value.lower()
    return _auth_type_key(value)


def auth_name_match_key(value: Any) -> str:
    """Build the casing-, space-, and dash-insensitive authentication name key."""
    return as_str(value).strip().lower().replace("-", "").replace(" ", "")


def auth_match_key(name: Any, auth_type: Any) -> tuple[str, str]:
    """Build the canonical authentication-method identity used throughout the product."""
    return (auth_name_match_key(name), auth_type_match_key(auth_type))


def auth_entity_key(name: Any, auth_type: Any) -> str:
    """Serialize the canonical authentication identity for relevance persistence."""
    return "|".join(auth_match_key(name, auth_type))
