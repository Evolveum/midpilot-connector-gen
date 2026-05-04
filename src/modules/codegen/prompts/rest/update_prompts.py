# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_update_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors for midPoint. Your goal is to prepare an `update` schema in Groovy. 

The input data you will receive:
1. A fragment that was extracted in the previous step LLM from the OpenAPI/Swagger attributes from api/v1/digester/{{session_id}}/attributes. Some attributes can be used in creating Groovy script.
2. A fragment that was extracted in the previous step LLM from the OpenAPI/Swagger endpoints from api/v1/digester/{{session_id}}/endpoints. Some endpoints can be used in creating Groovy script.
3. A chunk of the original document (e.g., API spec, model description, or related provider documentations) containing additional details that must be interpreted and incorporated—such as parameter semantics, data types, required vs optional fields, authentication hints, default values, example requests/responses, and error behavior.
4. Since the documentations does not fit into one chunk, you will receive Groovy code outputs from previous chunks so that you can complete or edit them.
5. Base API URL (if known) for path normalization is `{base_api_url}`.
6. Optional user-provided preferred endpoints in JSON are `{preferred_endpoints_json}`.

Prepare a valid Groovy code for update schema in Groovy based on the following `.adoc` documentations:

<update_docs>
{update_docs}
</update_docs>
""")
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- Maintain strict DSL scope: nested statements must stay inside their owning parent block and must not be moved to a higher level (for search, `supportedFilter`, `objectExtractor`, `pagingSupport`, `singleResult`, `emptyFilterSupported`, and request mutations stay inside `endpoint("...") {{ ... }}`).
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly. Never switch to a different class name (e.g., "User").
- Treat <extracted_attributes> and <extracted_endpoints> as the primary sources of truth. Prefer them over the examples in <update_docs>.
- If <preferred_endpoints> are provided, prioritize endpoints from this list whenever they are compatible with `<extracted_endpoints>` and docs.
- If <preferred_endpoints> conflict with `<extracted_endpoints>` or docs, prefer documented/extracted data and leave a short TODO comment about the mismatch.
- Endpoint paths used inside `endpoint("...")` MUST come from `<extracted_endpoints>` after normalization. Do not invent or copy path variants that are absent there.
- If docs show a versioned or absolute path variant (e.g., `/api/v3/users` or `https://host/api/v3/users`) but `<extracted_endpoints>` contains `/users`, you MUST use `/users`.
- For every `endpoint("...")`, output a path that starts with `/`, contains no scheme/host, and avoids duplicated base prefixes.
- If `base_api_url` contains a base path prefix (e.g., `/api/v1`), strip that prefix from endpoint paths when it appears in docs.
- If `update {{}}` contains multiple endpoints, each fully implemented endpoint MUST declare either `supportedAttributes ...` or `supportedAttribute("...") {{ ... }}`.
- Treat endpoint lifecycle actions as high-risk and strictly gated. Infer lifecycle intent from endpoint path, endpoint description, and endpoint `suggestedUse` (e.g., activate, deactivate, enable, disable, lock, unlock, suspend, unsuspend, block, unblock).
- When lifecycle attribute and target value are clearly documented, lifecycle endpoint MUST use `supportedAttribute("<attr>") {{ value <targetValue> }}` where `<attr>` and `<targetValue>` match documentation/extracted data.
- For lifecycle endpoints with incomplete evidence, you MAY keep an incomplete endpoint scaffold and TODO comments so later chunks can finish it. In that temporary scaffold, avoid guessing attribute names/values.
- If using an incomplete lifecycle scaffold, include clear TODO comments describing what is missing (attribute name, target value, request mapping). This scaffold may temporarily omit `supportedAttribute(...)` until evidence appears in a later chunk.
- Merge rule for iterative chunks: treat `<result>` as persistent memory from previous chunks. Information missing in the current chunk is NOT a reason to remove already-resolved configuration from `<result>`.
- No-regression rule: NEVER downgrade concrete working code to placeholders. If `<result>` already contains a concrete lifecycle mapping, do not replace it with TODO comments or commented-out values.
- You may change an existing concrete lifecycle value only when the current chunk provides explicit contradictory evidence. If evidence is ambiguous, keep the existing concrete value and add a short TODO about the conflict.
- Treat <result> as the current working Groovy code. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear.
- Preserve the outer objectClass and update blocks if already present in <result>.
- Return ONLY a valid format of the update schema in Groovy, including the inline comments as specified. No extra explanation outside the code block.
- The output format is just an example and may vary slightly based on the various specifications and documentations that will be available to you in the user prompt.
- No extra commentary.
""")
)

get_update_user_prompt = (
    textwrap.dedent("""
Chunk {idx}/{total} of the API schema:
Here is extracted object class attributes from OpenAPI/Swagger schema wrapped into JSON from previous LLM for {object_class}:

<extracted_attributes>
{attributes_json}
</extracted_attributes>

Here is extracted endpoints for object class from OpenAPI/Swagger schema wrapped into JSON from previous LLM for {object_class}:

<extracted_endpoints>
{endpoints_json}
</extracted_endpoints>

Optional user-provided preferred endpoints (JSON):

<preferred_endpoints>
{preferred_endpoints_json}
</preferred_endpoints>

Base API URL for endpoint-path normalization:

<base_api_url>
{base_api_url}
</base_api_url>
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
