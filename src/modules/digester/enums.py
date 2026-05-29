# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import re
from enum import StrEnum
from typing import Any


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RelevantLevel(StrEnum):
    TRUE = "true"
    FALSE = "false"
    MAYBE = "maybe"


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
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


_AUTH_TYPE_ALIASES: dict[str, AuthType] = {
    # basic
    "basic": AuthType.BASIC,
    "basicauth": AuthType.BASIC,
    "httpbasic": AuthType.BASIC,
    "httpbasicauth": AuthType.BASIC,
    "usernamepassword": AuthType.BASIC,
    # bearer
    "bearer": AuthType.BEARER,
    "bearertoken": AuthType.BEARER,
    "httpbearer": AuthType.BEARER,
    "httpbearertoken": AuthType.BEARER,
    "token": AuthType.BEARER,
    "accesstoken": AuthType.BEARER,
    # JWT bearer outside OAuth2 grant handling
    "jwt": AuthType.JWT_BEARER,
    "jsonwebtoken": AuthType.JWT_BEARER,
    "jwtbearer": AuthType.JWT_BEARER,
    "jwtbearertoken": AuthType.JWT_BEARER,
    "httpjwtbearer": AuthType.JWT_BEARER,
    "httpjwtbearertoken": AuthType.JWT_BEARER,
    # OAuth2 client credentials
    "clientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "clientcredentialsgrant": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauthclientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauth2clientcredentials": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    "oauth2clientcredentialsgrant": AuthType.OAUTH2_CLIENT_CREDENTIALS,
    # OAuth2 resource owner password credentials
    "oauth2password": AuthType.OAUTH2_PASSWORD,
    "oauth2passwordgrant": AuthType.OAUTH2_PASSWORD,
    "resourceownerpassword": AuthType.OAUTH2_PASSWORD,
    # OAuth2 JWT bearer grant
    "oauth2jwt": AuthType.OAUTH2_JWT,
    "oauth2jwtbearer": AuthType.OAUTH2_JWT,
    "oauth2jwtbearergrant": AuthType.OAUTH2_JWT,
    "jwtbearergrant": AuthType.OAUTH2_JWT,
    # OAuth2 SAML bearer grant
    "oauth2saml": AuthType.OAUTH2_SAML,
    "oauth2samlbearer": AuthType.OAUTH2_SAML,
    "oauth2samlbearergrant": AuthType.OAUTH2_SAML,
    # generic/unsupported OAuth2 flows
    "oauth": AuthType.OTHER,
    "oauth2": AuthType.OTHER,
    "oauth20": AuthType.OTHER,
    # api key
    "apikey": AuthType.API_KEY,
    "apikeyauth": AuthType.API_KEY,
    "xapikey": AuthType.API_KEY,
    "apiaccesskey": AuthType.API_KEY,
    # session
    "session": AuthType.SESSION,
    "cookie": AuthType.SESSION,
    "cookiesession": AuthType.SESSION,
    "sessioncookie": AuthType.SESSION,
    # digest
    "digest": AuthType.DIGEST,
    "httpdigest": AuthType.DIGEST,
    "httpdigestauth": AuthType.DIGEST,
    # hawk
    "hawk": AuthType.HAWK,
    "hawkauthentication": AuthType.HAWK,
    # AWS signature
    "aws": AuthType.AWS_SIGNATURE,
    "awssignature": AuthType.AWS_SIGNATURE,
    "awssignaturev4": AuthType.AWS_SIGNATURE,
    "aws4": AuthType.AWS_SIGNATURE,
    "awsv4": AuthType.AWS_SIGNATURE,
    "awssigv4": AuthType.AWS_SIGNATURE,
    "sigv4": AuthType.AWS_SIGNATURE,
    "aws4hmacsha256": AuthType.AWS_SIGNATURE,
    "awsiam": AuthType.AWS_SIGNATURE,
    # mtls
    "mtls": AuthType.MTLS,
    "mutualtls": AuthType.MTLS,
    "mutualtlsclientauthentication": AuthType.MTLS,
    "clientcertificate": AuthType.MTLS,
    "clientcert": AuthType.MTLS,
    # ntlm
    "ntlm": AuthType.NTLM,
    "ntlmauth": AuthType.NTLM,
    "windowsauth": AuthType.NTLM,
    "windowsauthentication": AuthType.NTLM,
    # openid connect
    "openidconnect": AuthType.OPENID_CONNECT,
    "openidconnectauth": AuthType.OPENID_CONNECT,
    "oidc": AuthType.OPENID_CONNECT,
    "openid": AuthType.OPENID_CONNECT,
    # fallback bucket
    "other": AuthType.OTHER,
    "custom": AuthType.OTHER,
    "unknown": AuthType.OTHER,
}
_AUTH_TYPE_ALIASES.update({_auth_type_key(auth_type.value): auth_type for auth_type in AuthType})


def is_known_auth_type(value: Any) -> bool:
    """Return True when a value maps to a supported auth type or alias."""
    return _auth_type_key(value) in _AUTH_TYPE_ALIASES


def normalize_auth_type(value: Any) -> AuthType:
    """Normalize auth type aliases to the public AuthType contract."""
    return _AUTH_TYPE_ALIASES.get(_auth_type_key(value), AuthType.OTHER)


def normalize_auth_type_value(value: Any, *, preserve_unknown: bool = False) -> str | None:
    """Normalize an auth type to its API value, optionally keeping unknown GUI input intact."""
    if value is None:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    if preserve_unknown and not is_known_auth_type(raw_value):
        return raw_value

    return normalize_auth_type(raw_value).value


def auth_type_match_key(value: Any) -> str:
    """Build a stable key for matching auth types across camelCase, kebab-case, and aliases."""
    if value is None:
        return ""
    if is_known_auth_type(value):
        return normalize_auth_type(value).value.lower()
    return _auth_type_key(value)


class EndpointType(StrEnum):
    CONSTANT = "constant"
    DYNAMIC = "dynamic"
    UNKNOWN = ""


class EndpointMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
