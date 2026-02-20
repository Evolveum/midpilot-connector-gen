# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# system prompt for <auth> extraction
get_auth_system_prompt = textwrap.dedent("""
    <instruction>
    You extract general, provider-agnostic authentication mechanisms from API documentation and OpenAPI security schemes.
    This task is about authentication (how clients prove identity), not authorization (roles/permissions/scopes policy).

    Extract ONLY complete, well-defined authentication methods. Focus on these standard types:
    
    1. **basic** - HTTP Basic Authentication (username:password in Base64)
    2. **bearer** - Bearer token authentication (JWT or other token in Authorization header)
    3. **oauth2** - OAuth 2.0 flows (authorization code, client credentials, PKCE, device code, implicit, password grant)
    4. **apiKey** - API key authentication (in header, query parameter, or cookie)
    5. **session** - Cookie-based session authentication
    6. **digest** - HTTP Digest Authentication
    7. **mtls** - Mutual TLS (client certificate authentication)
    8. **openidConnect** - OpenID Connect authentication
    9. **other** - Explicit auth mechanisms that do not fit the types above

    EXTRACTION RULES:
    - Extract ONLY authentication methods explicitly described in the documentation
    - Focus on GENERAL mechanisms, not provider-specific implementations
    - Normalize to standard types listed above when possible
    - The `type` field MUST be exactly one of: basic, bearer, oauth2, apiKey, session, digest, mtls, openidConnect, other
    - Include the method ONLY if it has sufficient detail (multiple sentences or clear implementation guidance)
    - Write quirks as a concise tutorial-style description explaining how to authenticate with this method, including any required headers, parameters, token formats, or special configuration needed
    
    QUALITY REQUIREMENTS:
    - Must be a complete authentication mechanism, not just a mention or reference
    - Must be applicable across different providers/systems (general patterns)
    - Must have clear implementation details in the documentation
    - If uncertain or details are vague, DO NOT include it
    
    AVOID:
    - Provider-specific brand names or implementations (e.g., "Auth0 login", "Okta SSO")
    - Incomplete mentions without implementation details
    - Authentication UI/UX descriptions without technical specifications
    - Generic security concepts not directly related to API authentication

    Return your findings using the structured output schema. Return an empty list if:
    - No authentication mechanisms are explicitly documented
    - Only provider-specific implementations are mentioned
    - Documentation lacks sufficient technical detail
    
    You will receive explicit format instructions; follow them exactly.
    </instruction>
    """)

# user prompt for <auth> extraction
get_auth_user_prompt = textwrap.dedent(
    """
Summary of the chunk:
 
<summary>
{summary}
</summary>

Tags of the chunk:
 
<tags>
{tags}
</tags>

Text from documentation:

<chunk>
{chunk}
</chunk>

Please extract authentication mechanisms present in this chunk. Use the structured output schema to respond.
Include the full name as written in the docs, a normalized type when obvious, and any notable quirks.
If nothing relevant is found, return an empty list.
"""
)
