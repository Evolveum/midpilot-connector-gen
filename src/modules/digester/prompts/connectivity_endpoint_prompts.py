# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_connectivity_endpoint_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst. Your goal is to find an HTTP endpoint in the documentation that can be used to
test connectivity between midPoint connector generator and the target application.

You will receive:
- a fragment of an OpenAPI/Swagger document or related API documentation,
- optional base API URL information,
- optional chunk summary and tags.

Use the structured output schema (ExtractedConnectivityEndpointResponse -> ExtractedConnectivityEndpointInfo).
Follow the format instructions exactly. If the fragment provides no suitable endpoint, return an empty endpoints list.

Selection goal:
- Choose an endpoint that is safe, cheap, and reliable for a connector "test connection" operation.
- The connector has NO knowledge of any existing resource IDs at this stage — it is establishing the initial
  connection before any data has been read. The endpoint must work without supplying a specific resource identifier.
- Prefer a documented read-only endpoint that validates both base URL reachability and configured authentication.
- For SCIM documentation, prefer ServiceProviderConfig, ResourceTypes, or Schemas endpoints if present.
- A GET collection endpoint (e.g. GET /users, GET /groups) is acceptable as a fallback when no dedicated
  connectivity/status endpoint exists, because it does not require a known resource ID.

Exclude:
- Any endpoint that requires a path parameter containing a specific resource identifier (e.g. GET /users/{{id}},
  GET /objects/{{objectId}}). There is no stable identifier available at connectivity test time.
- create/update/delete operations unless the documentation explicitly marks the endpoint as a safe test/ping endpoint.
- OAuth/token/login/logout endpoints. These are authentication flow endpoints, not target application connectivity
  checks.
- endpoints tied to heavy exports, reports, bulk operations, admin mutations, or asynchronous jobs.
- ambiguous paths or endpoints inferred only from prose without a documented method/path pair.

Path normalization:
- Output path must start with "/" and must not include scheme or host.
- If baseApiUrl contains an API prefix and the documented endpoint repeats that prefix, output the connector-relative
  path without duplicating the prefix.
- Preserve documented path parameters exactly when a parameter is unavoidable.

Fields:
- method: HTTP method exactly as documented. Prefer GET.
- path: normalized path only, no scheme/host.
- description: short reason why this endpoint is suitable for connectivity testing.
- responseContentType and requestContentType: fill only when documented or strongly implied by OpenAPI media types.
- requiresAuth: true if the endpoint requires configured authentication, false if explicitly public, null if unclear.

Confidence:
- Return only endpoints that are clearly supported by the provided fragment.
- It is better to return an empty list than to invent a connectivity endpoint.
""")


get_connectivity_endpoint_user_prompt = textwrap.dedent("""
Summary of the chunk:

<summary>
{summary}
</summary>

Tags of the chunk:

<tags>
{tags}
</tags>

Base API URL:

<base_api_url>
{base_api_url}
</base_api_url>

Text from documentation:

<chunk>
{chunk}
</chunk>

Find endpoint candidates from this fragment that can be used for a connector connectivity test.
Return only clearly documented candidates. If none are present, return an empty endpoints list.
""")


get_connectivity_endpoint_ranking_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst. Your task is to rank a list of HTTP endpoint candidates by their suitability
for use as a connector "test connection" operation.

A good connectivity test endpoint should:
- Be safe and read-only (GET preferred).
- Be cheap and fast — no side effects, no mutations.
- Work WITHOUT any known resource ID — the connector has no loaded data at this point.
- Validate both base URL reachability and configured authentication.
- Ideally confirm the identity of the caller (e.g. current user/profile, /me, whoami).
- Alternatively confirm the API is reachable and functioning (e.g. status, health, ping, version, metadata).
- A collection GET (e.g. GET /users, GET /groups) is acceptable when no dedicated status endpoint exists.
- For SCIM APIs: ServiceProviderConfig, ResourceTypes, or Schemas are ideal.

Penalize endpoints that:
- Require a path parameter with a specific resource identifier (e.g. /users/{{id}}) — no stable ID is available.
- Are write operations (POST, PUT, PATCH, DELETE) unless explicitly marked as test/ping.
- Are authentication flow endpoints (OAuth, token, login, logout).
- Are heavy operations (bulk, export, report, admin mutations, async jobs).

Return the endpoints in ranked order — most suitable first. Include all input candidates in the output.
""")


get_connectivity_endpoint_ranking_user_prompt = textwrap.dedent("""
Rank the following API endpoint candidates by their suitability as a connector connectivity test endpoint.
Most suitable first. Include all candidates in the output.

<candidates>
{candidates}
</candidates>

Return all {count} candidates in ranked order using the rankedEndpoints field.
""")
