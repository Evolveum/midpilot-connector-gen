# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt

get_authorization_system_prompt = build_operation_system_prompt(
    "authorization",
    rules=r"""
- Treat <selected_authorizations> as the exact set requested by the user. Generate only for those methods.
- Do not infer or generate unselected authentication/authorization alternatives from the documentation chunk.
- If a selected authorization has `analysisSupport: "unsupported"`, it was selected in midPoint but was not
  identified in the analyzed application documentation. Do not invent implementation details for that method; preserve
  only the existing explanatory comment for it.
- Match the style of the SCIMREST Builder API used by schema/search/create/update/delete artifacts: compact
  top-level builder blocks (Groovy) or the equivalent declarative-YAML keys, nested statements, and minimal
  imperative code.
- Use the exact authorization root shape: Groovy `authorization {{ {authentication_container} {{ ... }} }}`,
  or declarative YAML `authentication: {{ {authentication_container}: {{ ... }} }}`.
- For REST output, the second-level block/key must be `rest`. For SCIM output, it must be `scim`.
- There is no generic `oauth2` keyword. Use the grant-specific keyword matching the selected method:
  `oauth2ClientCredentials`, `oauth2Password`, `oauth2JwtBearer`, or `oauth2Saml` (Groovy blocks, or the
  matching declarative-YAML key). Each independently accepts the hook DSL:
  `oauth2ClientCredentials {{ oauth2Context -> validateToken {{ ... }} buildTokenRequest {{ request -> ... }} parseTokenResponse {{ response -> ... }} applyToken {{ request -> ... }} onResponse {{ response -> ... }} }}`
  (substitute the grant-specific keyword). Any hook not provided falls back to the built-in behavior.
- Use `request.formParam(...)` for token request form parameters and `request.header(...)` for request headers.
- Do not generate Java classes, imports, standalone helper methods, ad-hoc HTTP clients, XML resource configuration, or
  midPoint security-policy authorization XML.
- Preserve the semantic distinction between methods: bearer token, JWT bearer token, API key, Basic auth,
  OAuth2 client credentials, OAuth2 password, OAuth2 JWT bearer grant, OAuth2 SAML bearer grant, and other
  custom mechanisms can need different configuration properties and request customization. Digest, Hawk and
  NTLM have configuration properties but are not implemented by the framework - never generate a script or
  YAML block claiming to authenticate with them; treat a selection of one of these three as unsupported instead.
- Generate connector-level code, not objectClass CRUD/search code.
- Prefer existing `configuration.*` properties when examples or extracted notes imply built-in connector configuration using the exact namespace-specific properties from <authorization_docs>, such as configuration.restOAuth2ClientId or configuration.scimOAuth2ClientId. Never invent configuration.clientId or configuration.clientSecret.
- Implement request decoration for the selected method: `request.header(...)`, `request.formParam(...)`, documented query parameters, cookies, OAuth token exchange hooks, or mTLS setup as supported by the documentation.
- If the documentation does not provide enough detail for an executable implementation, keep a small valid scaffold with TODO comments for the missing values instead of inventing provider-specific behavior.
- Treat <result> as persistent accumulated code in whichever format it is already in. Extend or minimally edit it; do not discard already correct blocks just because the current chunk is silent.
- Token endpoints may use a separate authorization server. Preserve their documented absolute URL; never strip its host or apply the resource API base-path normalization to it.
- No extra commentary outside the fenced code block.

""",
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
