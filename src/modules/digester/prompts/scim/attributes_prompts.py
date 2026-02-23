# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# system prompt for SCIM <attributes> guided extraction
scim_attributes_system_prompt = textwrap.dedent(
    """
You are analyzing SCIM {object_class} attributes for an application that supports SCIM 2.0.

### CONTEXT: SCIM 2.0 Standard Attributes for {object_class}

{scim_base_attributes}

These attributes are ALREADY PROVIDED in the base schema. Do NOT extract them unless they have
deviations from the standard (see below).

### YOUR TASK: Extract ONLY custom attributes and deviations

You will receive documentation chunks. Extract ONLY:

1) **Custom Attributes** - Attributes NOT in the SCIM 2.0 standard
   Examples for User extensions:
   - slack_id, workspace_id (Slack-specific)
   - okta_id, status_changed (Okta-specific)
   - employee_status, hire_date (custom enterprise fields)

2) **Unsupported Standard Attributes** - Standard SCIM attributes that are NOT supported
   Identify if documentation explicitly states attributes are not implemented or unavailable.
   Examples:
   - "x509Certificates attribute is not supported"
   - "ims (instant messaging) is not available"

3) **Deviations from Standard** - Standard attributes with different behavior
   Examples:
   - userName is immutable (normally mutable)
   - emails.primary is required (normally optional)
   - password is readable (normally write-only)

### WHAT TO EXCLUDE

DO NOT extract:
- Standard SCIM attributes that work as documented (we already have them)
- Metadata fields (id, meta, schemas) - these are automatically handled
- Fields from other schemas (extract those when analyzing the other schema)

### OUTPUT FORMAT

Use the structured output schema (AttributeResponse with "attributes" field).

For custom attributes, provide full AttributeInfo:
- **type**: string, number, integer, boolean, object, array
- **format**: email, uri, date-time, binary, embedded, reference, etc.
- **description**: Clear description of the attribute
- **mandatory**: Is it required?
- **updatable**: Can it be modified after creation?
- **creatable**: Can it be set during creation?
- **readable**: Is it returned in responses?
- **multivalue**: Is it an array?
- **returnedByDefault**: Returned without explicit request?

For unsupported attributes, include a note in the description:
"[NOT SUPPORTED by this application]"

For deviations, override the relevant fields and note the difference.

### EXAMPLES

Example 1 - Custom attributes (Slack User extension):
```json
{{
  "attributes": {{
    "slack_id": {{
      "type": "string",
      "format": null,
      "description": "Unique Slack user identifier",
      "mandatory": false,
      "updatable": false,
      "creatable": false,
      "readable": true,
      "multivalue": false,
      "returnedByDefault": true
    }},
    "workspace_id": {{
      "type": "string",
      "format": null,
      "description": "Slack workspace identifier for this user",
      "mandatory": false,
      "updatable": false,
      "creatable": false,
      "readable": true,
      "multivalue": false,
      "returnedByDefault": true
    }}
  }}
}}
```

Example 2 - Unsupported attribute:
```json
{{
  "attributes": {{
    "x509Certificates": {{
      "type": "array",
      "format": null,
      "description": "X.509 certificates. [NOT SUPPORTED by this application]",
      "mandatory": false,
      "updatable": false,
      "creatable": false,
      "readable": false,
      "multivalue": true,
      "returnedByDefault": false
    }}
  }}
}}
```

Example 3 - Deviation (userName immutable):
```json
{{
  "attributes": {{
    "userName": {{
      "type": "string",
      "format": null,
      "description": "Unique user identifier. NOTE: Immutable after creation in this application.",
      "mandatory": true,
      "updatable": false,
      "creatable": true,
      "readable": true,
      "multivalue": false,
      "returnedByDefault": true
    }}
  }}
}}
```

If no custom attributes, unsupported attributes, or deviations are found, return an empty object.

Output must use the structured schema; do not add comments or prose.

"""
)

# user prompt for SCIM <attributes> guided extraction
scim_attributes_user_prompt = textwrap.dedent(
    """
Object Class: {object_class}

Summary of the chunk:

<summary>
{summary}
</summary>

Tags of the chunk:

<tags>
{tags}
</tags>

SCIM 2.0 Standard Attributes (for reference - do NOT extract these unless there are deviations):
{formatted_base_attributes}

Text from documentation:

<chunk>
{chunk}
</chunk>

Task:
Extract ONLY:
1. Custom attributes (not in SCIM standard)
2. Unsupported standard attributes
3. Standard attributes with deviations from SCIM 2.0 spec

Use the structured output schema (AttributeResponse). If none found in this chunk, return an empty object via the schema.
"""
)
