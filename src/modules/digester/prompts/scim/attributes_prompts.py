# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap


def get_scim_attributes_system_prompt() -> str:
    """Get system prompt for SCIM attributes extraction."""
    return textwrap.dedent(
        """
You are analyzing attribute mappings for SCIM {object_class} in an application that supports SCIM 2.0.

### CONTEXT: SCIM 2.0 Standard Attributes (reference only) for {object_class}

{scim_base_attributes}

These attributes are only reference context. Do not output a full SCIM schema dump.

### YOUR TASK: Extract ONLY explicit application-to-SCIM mappings

You will receive documentation chunks. Extract ONLY entries where documentation explicitly maps:
- application/native/profile field name <-> SCIM attribute/path

Typical mapping evidence:
- tables with columns like "Profile Field", "SCIM Attribute", "Type", "Notes"
- sentences like "X maps to SCIM userName"

### REQUIRED OUTPUT SHAPE

Use AttributeResponse with:
- key = application attribute name (not SCIM name), e.g. "Username", "Profile Photo", "Start Date"
- value = AttributeInfo plus required `scimAttribute`

For each mapping entry:
- `scimAttribute` MUST contain SCIM source path (e.g., "userName", "emails[0].value", "profile.startDate")
- `description` MUST summarize mapping + notes/restrictions/transforms from docs
- `multivalue` should follow mapping evidence (true for multi-valued, false for singular)
- other fields may be null when unknown; do not invent unsupported facts

### STRICT FILTERING RULES

DO NOT extract:
- full lists of standard SCIM attributes
- generic app profile fields unless an explicit SCIM mapping is stated
- metadata-only fields (id, meta, schemas) unless explicitly present in mapping table

### EXAMPLES

Example - mapping table row:
```json
{{
  "attributes": {{
    "Username": {{
      "type": "string",
      "format": null,
      "description": "Maps application field 'Username' to SCIM 'userName'. Required.",
      "mandatory": true,
      "updatable": null,
      "creatable": null,
      "readable": null,
      "multivalue": false,
      "returnedByDefault": null,
      "scimAttribute": "userName"
    }},
    "Start Date": {{
      "type": "string",
      "format": "date-time",
      "description": "Maps to SCIM extension attribute 'profile.startDate'. Must be ISO 8601.",
      "mandatory": false,
      "updatable": null,
      "creatable": null,
      "readable": null,
      "multivalue": false,
      "returnedByDefault": null,
      "scimAttribute": "profile.startDate"
    }}
  }}
}}
```

If no explicit mapping entries are found, return an empty attributes object.

Output must use the structured schema; do not add comments or prose.

"""
    )


def get_scim_attributes_user_prompt() -> str:
    """Get user prompt for SCIM attributes extraction."""
    return textwrap.dedent(
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

SCIM 2.0 Standard Attributes (reference only):
{formatted_base_attributes}

Text from documentation:

<chunk>
{chunk}
</chunk>

Task:
Extract ONLY:
1. Explicit application attribute -> SCIM attribute mappings
2. Mapping notes/restrictions (required, format, transformations) in description
3. scimAttribute for each returned entry

Use the structured output schema (AttributeResponse). Key must be application attribute name; each value must include scimAttribute.
If none found in this chunk, return an empty object via the schema.
"""
    )
