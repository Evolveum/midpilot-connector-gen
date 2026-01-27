# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_scim_delete_system_prompt = textwrap.dedent("""\
<instruction>
You are an expert in creating connectors (connID and midPoint) for SCIM 2.0 APIs. Your goal is to prepare a `delete` schema in Groovy for SCIM resources. Input will include:
1. A fragment that was extracted in the previous step LLM from the SCIM schema.
2. A fragment that was extracted in the previous step LLM from the SCIM endpoints.
3. A chunk of the original document (e.g., SCIM spec, provider documentation) containing additional details that must be interpreted and incorporated—such as soft delete behavior, error handling, and SCIM-specific constraints.
4. Since the documentation does not fit into one chunk, you will receive Groovy code outputs from previous chunks so that you can complete or edit them.
</instruction>

# SCIM 2.0 Delete Patterns:
<scim_patterns>
SCIM defines standard resource deletion:

**Request:**
- Method: DELETE
- Endpoint: /Users/{{id}} or /Groups/{{id}}
- No request body required
- Resource is identified by ID in path

**Response:**
- Status: 204 No Content (successful deletion)
- No response body
- Or 200 OK with optional response body

**Delete Behavior:**
1. **Hard Delete (Standard):**
   - Resource is permanently removed
   - Subsequent GET returns 404

2. **Soft Delete (Provider-specific):**
   - Some providers use PATCH to set active=false instead of DELETE
   - Check provider documentation for soft delete behavior
   - Resource may remain retrievable but marked as inactive

**Error Cases:**
- 404 Not Found: Resource doesn't exist
- 403 Forbidden: Insufficient permissions
- 409 Conflict: Resource cannot be deleted (e.g., has dependencies)

**Referential Integrity:**
- Deleting a User may affect Group memberships
- Deleting a Group removes all memberships
- Provider-specific cascade behavior
</scim_patterns>

# Reference documentation injected from .adoc:
<delete_docs>
{delete_docs}
</delete_docs>

Output rules:
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly. Never switch to a different class name (e.g., "User").
- SCIM deletes use DELETE method to the resource endpoint with ID: DELETE /Users/{{id}}.
- Typically returns 204 No Content with no response body.
- Consider soft delete behavior if provider uses active=false pattern.
- Treat <extracted_attributes> and <extracted_endpoints> as the primary sources of truth.
- Treat <result> as the current working Groovy code. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, add a TODO comment instead of guessing.
- Preserve the outer objectClass and delete blocks if already present in <result>.
- Return ONLY valid Groovy code with inline comments as needed. No extra explanation outside the code block.
- No extra commentary.
""")

get_scim_delete_user_prompt = textwrap.dedent("""
            Chunk {idx}/{total} of the SCIM schema:
            Target object class: {object_class}

            Here is extracted object class attributes from SCIM schema wrapped into JSON from previous LLM:

            <extracted_attributes>
            {attributes_json}
            </extracted_attributes>

            Here is extracted endpoints for object class from SCIM schema wrapped into JSON:

            <extracted_endpoints>
            {endpoints_json}
            </extracted_endpoints>

            Here is chunk where you have to find additional information:
            <chunk>
            {chunk}
            </chunk>

            Result from previous chunks:
            <result>
            {result}
            </result>
""")
