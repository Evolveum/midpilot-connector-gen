# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_scim_update_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (connID and midPoint) for SCIM 2.0 APIs. Your goal is to prepare an `update` schema in Groovy for SCIM resources. 

The input data you will receive:
1. A fragment that was extracted in the previous step LLM from the SCIM attributes for {object_class}.
2. A chunk of the original document (e.g., SCIM spec, model description, or related provider documentations) containing additional details that must be interpreted and incorporated, such as parameter semantics, data types, required vs optional fields, authentication hints, default values, example requests/responses, error behavior, mutability rules, PATCH operations, and SCIM-specific behavior.
3. Since the documentations does not fit into one chunk, you will receive Groovy code outputs from previous chunks so that you can complete or edit them.
4. Optional user-provided preferred endpoints in JSON are `{preferred_endpoints_json}`.

Prepare a valid Groovy code for update schema in Groovy based on the following `.adoc` documentations:

<update_docs>
{update_docs}
</update_docs>
""")
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

Output rules:
- Maintain strict DSL scope: nested statements must stay inside their owning parent block and must not be moved to a higher level (for search, `supportedFilter`, `objectExtractor`, `pagingSupport`, `singleResult`, `emptyFilterSupported`, and request mutations stay inside `endpoint("...") {{ ... }}`).
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly. Never switch to a different class name (e.g., "User").
- SCIM updates typically use PATCH with PatchOp schema for partial updates, or PUT for full replacement.
- Handle multi-valued complex attributes with path selectors when needed.
- Exclude readOnly and immutable attributes from updates.
- Treat <extracted_attributes> as the primary sources of truth. Prefer them over the examples in <update_docs>.
- If <preferred_endpoints> are provided, prioritize endpoints from this list whenever they are compatible with SCIM behavior and docs.
- If <preferred_endpoints> conflict with docs or SCIM semantics, prefer documented behavior and leave a short TODO comment about the mismatch.
- Treat <result> as the current working Groovy code. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, add a TODO comment instead of guessing.
- Preserve the outer objectClass and update blocks if already present in <result>.
- Return ONLY a valid format of the update schema in Groovy, including the inline comments as specified. No extra explanation outside the code block.
- The output format is just an example and may vary slightly based on the various specifications and documentations that will be available to you in the user prompt.
- No extra commentary.
""")
)

get_scim_update_user_prompt = (
    textwrap.dedent("""
Chunk {idx}/{total} of the SCIM schema:
Target object class: {object_class}

Here is extracted object class attributes from SCIM schema wrapped into JSON from previous LLM:

<extracted_attributes>
{attributes_json}
</extracted_attributes>

Optional user-provided preferred endpoints (JSON):

<preferred_endpoints>
{preferred_endpoints_json}
</preferred_endpoints>
""")
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Here is chunk where you have to find additional information:
<chunk>
{chunk}
</chunk>

Result from previous chunks:
<result>
{result}
</result>
""")
)
