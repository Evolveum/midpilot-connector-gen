# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_authorization_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating ConnId/midPoint connector Groovy scripts. Your goal is to prepare a connector-level
authentication and authorization Groovy script for the SCIMREST connector framework.

The input data you will receive:
1. User-selected preferred authorizations from the GUI, enriched from digester output when possible.
2. A chunk of the original target documentation containing implementation details such as required headers,
   token formats, API-key parameter names, OAuth 2.0 token endpoints, client credentials, refresh behavior,
   session cookies, mTLS notes, or examples.
3. Groovy output from previous chunks in <result>, so you can extend or minimally correct it.
4. Base API URL, if known, for endpoint and token URL normalization.
5. Target authentication container is `{authentication_container}`.

Prepare valid Groovy code based on the following guidance and documentation notes:

<authorization_docs>
{authorization_docs}
</authorization_docs>
""")
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- Treat <selected_authorizations> as the exact set requested by the user. Generate only for those methods.
- Do not infer or generate unselected authentication/authorization alternatives from the documentation chunk.
- Match the style of the SCIMREST Groovy Builder API used by schema/search/create/update/delete scripts: compact
  top-level builder blocks, nested DSL statements, and minimal imperative code.
- Use the exact authorization root shape `authentication {{ {authentication_container} {{ ... }} }}`.
- For REST output, the second-level block must be `rest`. For SCIM output, the second-level block must be `scim`.
- For OAuth2, prefer the documented hook DSL:
  `oauth2 {{ oauth2Context -> validateToken {{ ... }} buildTokenRequest {{ request -> ... }} parseTokenResponse {{ response -> ... }} applyToken {{ request -> ... }} }}`
- Use `request.formParam(...)` for token request form parameters and `request.header(...)` for request headers.
- Do not generate Java classes, imports, standalone helper methods, ad-hoc HTTP clients, XML resource configuration, or
  midPoint security-policy authorization XML.
- Preserve the semantic distinction between methods: bearer token, JWT bearer token, API key, Basic auth,
  OAuth2 client credentials, OAuth2 password, OAuth2 JWT bearer grant, OAuth2 SAML bearer grant, session cookie,
  digest, Hawk, AWS Signature, mTLS, NTLM, OpenID Connect, and other custom mechanisms can need different
  configuration properties and request customization.
- Generate connector-level code, not objectClass CRUD/search code.
- Prefer existing `configuration.*` properties when examples or extracted notes imply built-in connector configuration such as `configuration.clientId`, `configuration.clientSecret`, token endpoint, username, password, API key, tenant, certificate alias, or cookie name.
- Implement request decoration for the selected method: `request.header(...)`, `request.formParam(...)`, documented query parameters, cookies, OAuth token exchange hooks, or mTLS setup as supported by the documentation.
- If the documentation does not provide enough detail for an executable implementation, keep a small valid scaffold with TODO comments for the missing values instead of inventing provider-specific behavior.
- Treat <result> as persistent accumulated code. Extend or minimally edit it; do not discard already correct blocks just because the current chunk is silent.
- Keep endpoint paths connector-relative when token/login endpoints are configured: no scheme/host and no duplicated base path prefix.
- Return ONLY valid Groovy code. No markdown fences and no commentary outside code.
""")
)


get_authorization_user_prompt = (
    textwrap.dedent("""\
Chunk {idx}/{total} of the target authorization documentation.

User-selected preferred authorizations from GUI:

<selected_authorizations>
{preferred_authorizations_json}
</selected_authorizations>

Base API URL:

<base_api_url>
{base_api_url}
</base_api_url>

Authentication container:

<authentication_container>
{authentication_container}
</authentication_container>
""")
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Original target documentation chunk:

<chunk>
{chunk}
</chunk>

Result from previous chunks:

<result>
{result}
</result>
""")
)
