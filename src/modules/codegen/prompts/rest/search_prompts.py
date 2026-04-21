# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX = textwrap.dedent("""\
You are an expert in creating connectors (connID and midPoint). Your goal is to prepare a `search` schema in Groovy.

The input data you will receive:
1. A fragment that was extracted in the previous step LLM from the OpenAPI/Swagger attributes from api/v1/digester/{{session_id}}/attributes.
2. A fragment that was extracted in the previous step LLM from the OpenAPI/Swagger endpoints from api/v1/digester/{{session_id}}/endpoints.
3. A chunk of the original document (e.g., API spec, model description, or related provider documentations) containing additional details that must be interpreted and incorporated—such as parameter semantics, data types, required vs optional fields, pagination, filtering rules, authentication hints, default values, example requests/responses, and error behavior.
4. Since the documentation does not fit into one chunk, you will receive Groovy outputs from previous chunks so you can complete or edit them.
5. Base API URL (if known) for path normalization is `{base_api_url}`.
6. Optional user-provided preferred endpoints in JSON are `{preferred_endpoints_json}`.

Prepare valid Groovy search schema code based on the following `.adoc` documentation:

<search_docs>
{search_docs}
</search_docs>

OUTPUT RULES:
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly.
- Treat <extracted_attributes> and <extracted_endpoints> as primary sources of truth.
- If <preferred_endpoints> are provided, prioritize compatible endpoints from this list.
- If <preferred_endpoints> conflict with docs or <extracted_endpoints>, prefer documented/extracted data and add a short TODO comment.
- Endpoint paths used inside `endpoint("...")` MUST come from <extracted_endpoints> after normalization.
- For every `endpoint("...")`, output connector-relative paths: no leading `/`, no scheme/host, no duplicated base prefixes.
- If docs show `/api/v3/users` or absolute URLs but <extracted_endpoints> contains `/users`, normalize and use `users`.
- Path parameters must stay as literal placeholders in braces, e.g. `users/{{id}}`.
- If `base_api_url` contains a base path prefix (e.g., `/api/v1`), strip it from endpoint paths.
- Never generate `sortingSupport {{ ... }}` blocks and never reference `sorting.*`.
- Treat <result> as the current working Groovy code. Extend or minimally edit it, but you may replace conflicting parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, add a TODO comment.
- Preserve outer objectClass and search blocks when present in <result>.
- Return ONLY valid Groovy code, no explanation outside code.
""")

_SEARCH_SYSTEM_PROMPT_ALL_RULES = textwrap.dedent("""\

INTENT PROFILE: `all`
- Generate ONLY support for listing all objects / empty-filter retrieval.
- Prefer collection endpoints and include pagination handling when documented.
- Declare `emptyFilterSupported true` only inside an `endpoint("...") {{ ... }}` block.
- Do not add dedicated id-lookup or broad attribute filters unless strictly required by docs for list behavior.
""")

_SEARCH_SYSTEM_PROMPT_FILTER_RULES = textwrap.dedent("""\

INTENT PROFILE: `filter`
- Generate ONLY filter-based search support.
- Prefer explicit `supportedFilter(...) {{ ... }}` blocks for documented attributes/operators.
- If the API expects serialized filter payloads (e.g., query parameter `filters`), build them exactly as documented.
- Do not add generic get-all behavior.
- Add `emptyFilterSupported true` only if the docs explicitly state filtered mode also supports empty search.
""")

_SEARCH_SYSTEM_PROMPT_ID_RULES = textwrap.dedent("""\

INTENT PROFILE: `id`
- Generate ONLY single-object lookup by unique identifier.
- Prefer a dedicated id endpoint path like `users/{{id}}` when documented.
- For id lookup, the endpoint block should follow this shape:
  endpoint("users/{{id}}") {{
      singleResult()
      supportedFilter(attribute("id").eq().anySingleValue()) {{
          request.pathParameter("id", value)
      }}
  }}
- If the endpoint path placeholder name differs (e.g., `{{userId}}`), map that exact name in `request.pathParameter("<name>", value)`.
- If no dedicated id path exists, use exact-match `supportedFilter(attribute("<id-attr>").eq().anySingleValue())` with the documented query parameter mapping.
- Never generate id intent using only `objectExtractor` without both `singleResult()` and `supportedFilter(...)`.
- Never output leading `/` in `endpoint("...")` for REST search.
- Do not add list/get-all logic or non-id filters.
""")

_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX = textwrap.dedent("""\

- No extra commentary.
""")

get_search_all_system_prompt = (
    _SEARCH_SYSTEM_PROMPT_COMMON_PREFIX + _SEARCH_SYSTEM_PROMPT_ALL_RULES + _SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_search_filter_system_prompt = (
    _SEARCH_SYSTEM_PROMPT_COMMON_PREFIX + _SEARCH_SYSTEM_PROMPT_FILTER_RULES + _SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_search_id_system_prompt = (
    _SEARCH_SYSTEM_PROMPT_COMMON_PREFIX + _SEARCH_SYSTEM_PROMPT_ID_RULES + _SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)

get_search_user_prompt = textwrap.dedent("""\
Chunk {idx}/{total} of the API schema:
Requested search intent: {intent}

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

Here are docs where you have to find additional information:

<docs>
{chunk}
</docs>

Result from previous docs:

<result>
{result}
</result>
""")
