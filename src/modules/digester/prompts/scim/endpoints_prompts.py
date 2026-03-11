# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# system prompt for SCIM <endpoints> guided extraction
scim_endpoints_system_prompt = textwrap.dedent(
    """
You are analyzing SCIM {object_class} endpoints for an application that supports SCIM 2.0.

### CONTEXT: SCIM 2.0 Standard Endpoints for {object_class}

{scim_base_endpoints}

These endpoints are ALREADY PROVIDED in the base schema. Do NOT extract them unless they have
deviations or custom behavior (see below).

### YOUR TASK: Extract ONLY custom endpoints and deviations

You will receive documentation chunks. Extract ONLY:

1) **Custom Endpoints** - Endpoints NOT in SCIM 2.0 standard
   Examples:
   - GET /{resource}/{{id}}/activate (custom lifecycle operation)
   - POST /{resource}/{{id}}/suspend (custom action)
   - GET /{resource}/{{id}}/sessions (additional sub-resources)
   - POST /{resource}/search (custom search endpoint)

2) **Unsupported Standard Endpoints** - Standard SCIM endpoints that DON'T work
   Examples:
   - "PATCH is not supported" (only PUT for updates)
   - "DELETE is not available" (soft delete only via PATCH)
   - "Bulk operations not implemented"

3) **Custom Query Parameters** - Non-standard filters, pagination, sorting
   Examples:
   - Custom filter operators beyond SCIM spec
   - Additional query parameters (e.g., includeDeleted, expand)
   - Different pagination mechanisms

4) **Deviations from Standard** - Standard endpoints with different behavior
   Examples:
   - PUT requires full resource (differs from partial update semantics)
   - GET /{resource}?filter= uses different syntax
   - Special authentication requirements for certain endpoints

### WHAT TO EXCLUDE

DO NOT extract:
- Standard SCIM CRUD endpoints (GET /{resource}, POST /{resource}, etc.) unless they have deviations
- Protocol endpoints (/ServiceProviderConfig, /Schemas, /ResourceTypes, /Bulk) unless custom behavior
- Standard SCIM filter/pagination that works as documented

### OUTPUT FORMAT

Use the structured output schema (EndpointResponse with "endpoints" field).

For each endpoint, provide:
- **path**: URL path with parameters in {{curly braces}}
- **method**: HTTP method (GET, POST, PUT, PATCH, DELETE)
- **description**: What this endpoint does (include deviation notes if applicable)
- **responseContentType**: application/scim+json or other
- **requestContentType**: application/scim+json or other
- **suggestedUse**: List of use cases (create, update, delete, getById, getAll, search, custom actions)

### EXAMPLES

Example 1 - Custom endpoint (activate user):
```json
{{
  "endpoints": [
    {{
      "path": "/{{base_api_url}}/Users/{{id}}/activate",
      "method": "POST",
      "description": "Activate a suspended user account",
      "responseContentType": "application/scim+json",
      "requestContentType": null,
      "suggestedUse": ["activate"]
    }}
  ]
}}
```

Example 2 - Unsupported endpoint (PATCH not supported):
```json
{{
  "endpoints": [
    {{
      "path": "/{{base_api_url}}/Users/{{id}}",
      "method": "PATCH",
      "description": "[NOT SUPPORTED] PATCH operations are not available. Use PUT for updates.",
      "responseContentType": null,
      "requestContentType": null,
      "suggestedUse": []
    }}
  ]
}}
```

Example 3 - Custom query parameters:
```json
{{
  "endpoints": [
    {{
      "path": "/{{base_api_url}}/Users",
      "method": "GET",
      "description": "Retrieve users with custom filtering. Supports SCIM filter syntax plus custom 'includeDeleted' parameter.",
      "responseContentType": "application/scim+json",
      "requestContentType": null,
      "suggestedUse": ["getAll", "search"],
      "customQueryParameters": {{
        "includeDeleted": {{
          "type": "boolean",
          "description": "Include soft-deleted users in results",
          "required": false
        }},
        "expand": {{
          "type": "string",
          "description": "Comma-separated list of related resources to expand (e.g., 'groups,roles')",
          "required": false
        }}
      }}
    }}
  ]
}}
```

Example 4 - Deviation (different filter syntax):
```json
{{
  "endpoints": [
    {{
      "path": "/{{base_api_url}}/Users",
      "method": "GET",
      "description": "Retrieve users with filtering. NOTE: This application uses a custom filter syntax that differs from standard SCIM filters. Use 'search=<term>' instead of 'filter=<expression>'.",
      "responseContentType": "application/scim+json",
      "requestContentType": null,
      "suggestedUse": ["getAll", "search"]
    }}
  ]
}}
```

If no custom endpoints, unsupported endpoints, or deviations are found, return an empty list.

Output must use the structured schema; do not add comments or prose.

"""
)

# user prompt for SCIM <endpoints> guided extraction
scim_endpoints_user_prompt = textwrap.dedent(
    """
Object Class: {object_class}
Base API URL: {base_api_url}

Summary of the chunk:

<summary>
{summary}
</summary>

Tags of the chunk:

<tags>
{tags}
</tags>

SCIM 2.0 Standard Endpoints (for reference - do NOT extract these unless there are deviations):
{formatted_base_endpoints}

Text from documentation:

<chunk>
{chunk}
</chunk>

Task:
Extract ONLY:
1. Custom endpoints (not in SCIM standard)
2. Unsupported standard endpoints
3. Custom query parameters or filtering mechanisms
4. Standard endpoints with deviations from SCIM 2.0 spec

Use the structured output schema (EndpointResponse). If none found in this chunk, return an empty list via the schema.
"""
)
