# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_info_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst. Extract *high-level* application and API metadata from a documentation fragment.

You will receive explicit format instructions; follow them exactly.

You MUST produce output that fits the structured schema (InfoResponse - InfoMetadata - BaseAPIEndpoint).
If the fragment provides nothing relevant, return InfoResponse with info_about_schema = null.

Populate fields ONLY when clearly supported by the fragment. Do not copy ambiguous values.

RULES:
1) name
   - Application/product name exactly as in docs. Keep original casing.
   - Prefer explicit product names over organization/vendor names.

2) applicationVersion
   - The *product/application* version (e.g., "15.2", "2024.2").
   - DO NOT use standards versions (e.g., "OpenAPI 3.0", "SCIM 2.0") here.
   - If not present, leave empty string "".

3) apiVersion
   - The *API* version (e.g., "v1", "1", "2024-05").
   - Normalize by stripping leading "v"/"V" if the intent is a plain number (e.g., "v3" → "3").
   - Typical sources: "servers.url" path segment ("/api/v3"), "basePath" or explicit "API version" notes.
   - If not present, leave empty string "".

4) apiType
   - A list with normalized technology labels, chosen from:
     REST, OpenAPI, SCIM, SOAP, GraphQL, Other
   - Deduplicate. Examples:
     - "OpenAPI 3" ⇒ include "OpenAPI"
     - "REST API" ⇒ include "REST"
     - "SCIM 2.0" ⇒ include "SCIM"
     - If unclear, include "Other" only when the API type is clearly not one of the above.

5) baseApiEndpoint
   GOAL: Provide one or more canonical *base API URLs* suitable for connectivity checks or discovery, NOT specific resource paths.
   - Prefer the global API base root + version if applicable (e.g., "https://<hostname>/api/v3/").
   - Sources (in priority order):
     a) OpenAPI "servers[].url" (respect variables/templates),
     b) OpenAPI 2.0 "schemes"+"host"+"basePath",
     c) Explicit "Base URL", "API endpoint", or similar in docs.
   - Canonicalization:
     * Replace any concrete hostname with a template host: "<hostname>" unless docs state a single global hostname for all tenants.
     * Keep HTTPS if available; otherwise use the given scheme.
     * Keep only the API root and version segment (e.g., "/api/", "/api/v3/", "/rest/", "/graph/"). 
       Remove resource parts like "/users", "/projects/123", query strings, and fragments.
     * Ensure exactly one trailing slash.
   - Classification:
     * "type": "dynamic" if the hostname or tenant can vary (default unless explicitly constant across all deployments).
     * "type": "constant" only if docs assert a single, global, non-tenant URL for everyone.
   - Return ALL distinct canonical base endpoints supported by evidence in docs.
   - Deduplicate by (uri, type) and sort the final list by uri ascending, then type (constant before dynamic).

MERGE & CONFIDENCE:
- You will receive the previously aggregated JSON. Only update fields when the current chunk provides stronger or clearer evidence.
- Keep previously extracted values if the new chunk is weaker or ambiguous.
- For baseApiEndpoint specifically, keep existing valid entries and append newly supported distinct entries.
- When uncertain, prefer leaving the field unchanged rather than guessing.

COMMON PITFALLS TO AVOID
- Do NOT set applicationVersion = "3" just because the docs say "OpenAPI 3".
- Do NOT return a resource path as baseApiEndpoint (e.g., "/api/v3/users"). Keep the API base root.
- Do NOT invent values. If unknown, leave empty or null per schema.
""")


get_info_user_prompt = textwrap.dedent("""
Summary of the chunk:

<summary>
{summary}
</summary>

Tags of the chunk:

<tags>
{tags}
</tags>

Text from actual documentation:

<chunk>
{chunk}
</chunk>

Result from previous chunks:

<already_extracted>
{aggregated_json}
</already_extracted>

Update the structured output using this fragment:
- Start from <already_extracted> and only modify fields that this fragment clarifies or corrects.
- Apply the FIELD RULES for name, applicationVersion, apiVersion, apiType, and baseApiEndpoint.
- For baseApiEndpoint, keep a deduplicated sorted list of canonical base URLs (template host "<hostname>", API root + optional version, trailing slash; classify type as "dynamic" unless the docs guarantee a single global URL).
- If this fragment adds nothing reliable, return the aggregated object unchanged.
""")
