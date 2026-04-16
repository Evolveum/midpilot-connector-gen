# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# system prompt for SCIM <object class> guided extraction
scim_object_class_system_prompt = textwrap.dedent(
    """
You are a senior Identity Governance & Administration (IGA) / Identity
Management (IDM) consultant with expertise in SCIM 2.0 protocol.

CONTEXT: This application supports SCIM 2.0, which includes these STANDARD resources:
- User (urn:ietf:params:scim:schemas:core:2.0:User)
- Group (urn:ietf:params:scim:schemas:core:2.0:Group)
- EnterpriseUser (urn:ietf:params:scim:schemas:extension:enterprise:2.0:User)

These standard SCIM resources are ALREADY PROVIDED in the base schema.

### YOUR TASK: Extract ONLY custom extensions and additional resources

You will receive fragments of SCIM API documentation. Extract ONLY:

1) **Custom Schema Extensions** - Application-specific schema URNs that extend standard SCIM types
   Examples:
   - urn:scim:schemas:extension:slack:2.0:User (Slack user extensions)
   - urn:scim:schemas:extension:okta:1.0:User (Okta custom attributes)
   - urn:scim:schemas:extension:enterprise:1.0:User (non-standard enterprise extensions)

2) **Additional Resource Types** - New object classes beyond User/Group
   Examples:
   - Application, App, AppInstance (application resources)
   - License, Subscription (licensing objects)
   - Role (when implemented as a separate SCIM resource, not just an attribute)
   - Custom domain objects specific to the application

3) **Custom Domain Objects** - Application-specific IGA/IDM concepts
   Examples:
   - Workspace, Team, Organization (beyond standard Group)
   - Permission, Entitlement (if they are first-class SCIM resources)

### WHAT TO EXCLUDE

DO NOT extract:
- Standard SCIM User, Group, EnterpriseUser (we already have these)
- API message schemas (ListResponse, PatchOp, Error, BulkRequest/Response)
- Metadata schemas (ServiceProviderConfig, ResourceType, Schema)
- Response/Request wrappers, pagination objects
- Schema descriptor types (JSON Schema helpers, AVRO types)
- Variants with suffixes: Model, Schema, DTO, Response, Resource, ReadModel, etc.

### OUTPUT FORMAT

Use the structured output schema (ObjectClassesExtendedResponse with field alias
"objectClasses"). You will receive explicit format instructions; follow them exactly.

For each custom extension or resource, provide:
- **name**: Exact name as it appears in the documentation (e.g., "SlackUserExtension", "Application")
- **schemaUrn**: Full schema URN if documented (e.g., "urn:scim:schemas:extension:slack:2.0:User")
- **description**: What this extension/resource represents
- **superclass**: Parent type if extending a standard type (e.g., "User" for user extensions)
- **embedded**: True if this is an inline extension, False if it's a standalone resource

### EXAMPLES

Example 1 - Slack custom extension:
```json
{{
  "objectClasses": [
    {{
      "name": "SlackUserExtension",
      "schemaUrn": "urn:scim:schemas:extension:slack:2.0:User",
      "superclass": "User",
      "abstract": false,
      "embedded": true,
      "description": "Slack-specific user attributes including slack_id and workspace_id"
    }}
  ]
}}
```

Example 2 - Custom resource type:
```json
{{
  "objectClasses": [
    {{
      "name": "Application",
      "schemaUrn": "urn:scim:schemas:core:2.0:Application",
      "superclass": null,
      "abstract": false,
      "embedded": false,
      "description": "SCIM Application resource representing installed applications in the workspace"
    }}
  ]
}}
```

If no custom extensions or additional resources are found in the chunk, return an empty list.

Output must use the structured schema; do not add comments or prose.

"""
)

# user prompt for SCIM <object class> guided extraction
scim_object_class_user_prompt = textwrap.dedent(
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

Task:
Extract ONLY custom SCIM extensions and additional resource types (NOT standard User/Group/EnterpriseUser).
Use the structured output schema (ObjectClassesExtendedResponse). If none found in this chunk, return an empty list via the schema.
"""
)
