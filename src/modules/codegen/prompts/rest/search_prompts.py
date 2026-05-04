# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX = (
    textwrap.dedent("""\
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
""")
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- Maintain strict DSL scope: nested statements must stay inside their owning parent block and must not be moved to a higher level (for search, `supportedFilter`, `objectExtractor`, `pagingSupport`, `singleResult`, `emptyFilterSupported`, and request mutations stay inside `endpoint("...") {{ ... }}`).
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly.
- Treat <extracted_attributes> and <extracted_endpoints> as primary sources of truth.
- If <preferred_endpoints> are provided, prioritize compatible endpoints from this list.
- If <preferred_endpoints> conflict with docs or <extracted_endpoints>, prefer documented/extracted data and add a short TODO comment.
- Endpoint paths used inside `endpoint("...")` MUST come from <extracted_endpoints> after normalization.
- For every `endpoint("...")`, output connector-relative paths: no leading `/`, no scheme/host, no duplicated base prefixes.
- If docs show `/api/v3/users` or absolute URLs but <extracted_endpoints> contains `/users`, normalize and use `users`.
- Path parameters must stay as literal placeholders in braces, e.g. `users/{{id}}`.
- If `base_api_url` contains a base path prefix (e.g., `/api/v1`), strip it from endpoint paths.
- Only use request parameters from documentation for the same normalized endpoint path and HTTP method being generated.
- Ignore parameters, examples, supported filter lists, and filter payload formats from unrelated endpoints in the same chunk.
- Do not treat <extracted_attributes> as proof that an attribute can be used in a filter; extracted attributes only constrain names/types after the endpoint's own documentation proves filter support.
- Never generate `sortingSupport {{ ... }}` blocks and never reference `sorting.*`.
- Treat <result> as the current working Groovy code. Extend or minimally edit it, but you may replace conflicting parts.
- Treat concrete code already present in <result> as accumulated evidence from earlier chunks. Preserve existing endpoint blocks, `objectExtractor`, `pagingSupport`, `singleResult`, `emptyFilterSupported`, and executable `supportedFilter(...) {{ ... }}` blocks unless the current chunk gives explicit same-endpoint evidence that they are wrong.
- A current chunk that omits filters, pagination, extraction details, or an endpoint parameter list is not evidence that previously generated code is unsupported. If the current chunk adds no relevant or contradictory evidence, return <result> unchanged.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, add a TODO comment.
- Preserve outer objectClass and search blocks when present in <result>.
- Return ONLY valid Groovy code, no explanation outside code.
""")
)

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
- A REST filter is documented only when the target endpoint's own GET documentation or <extracted_endpoints> explicitly includes a filter-capable request parameter (for example `filter`, `filters`, or an attribute-specific query parameter) and documents the specific filter attribute/operator or gives an example for that same endpoint.
- Use `supportedFilter(attribute("<attr>").<op>().anySingleValue()) {{ ... }}` for each documented filter.
- Map operators to ConnId filter ops (for example exact match -> `.eq()`, contains -> `.contains()`), creating separate blocks when needed.
- If the API expects serialized filter payloads (e.g., query parameter `filters`), build them inside each `supportedFilter` block exactly as documented.
- Keep `pagingSupport` only for pagination parameters; do not place filter parameters there.
- Do not add generic get-all behavior.
- Add `emptyFilterSupported true` only if the docs explicitly state filtered mode also supports empty search.
- Do not infer filters from response fields, <extracted_attributes>, schema properties, sort fields, select fields, or examples for other endpoints.
- Description-only hints such as "can choose to filter similar to ..." are insufficient unless the same endpoint also documents the filter request parameter and supported filter keys.
- If the current chunk's matching endpoint parameter list is present and contains no filter-capable parameter, do not add new filters from that chunk.
- Do not remove `supportedFilter(...)` blocks already present in <result> unless the current chunk explicitly documents, for the same normalized endpoint path and HTTP method, that the previous filter parameter or previous filter attribute is invalid or unsupported.
- If no documented filters are found in the current chunk, leave <result> unchanged, including existing endpoint, `objectExtractor`, `pagingSupport`, and `supportedFilter(...)` blocks.
- Use ConnId-compatible filter DSL only:
  - `supportedFilter(attribute("<attr>").eq().anySingleValue()) {{ ... }}`
  - `supportedFilter(attribute("<attr>").contains().anySingleValue()) {{ ... }}`
- Each `supportedFilter(...)` block must mutate `request` and use the provided `value`.
- If API expects serialized filter payload (for example query parameter `filters`), build the payload inside each filter block, e.g.:
  - `String filter = "[{{ \\"name\\": {{ \\"operator\\": \\"=\\", \\"values\\": [\\"${{value}}\\"] }} }}]"`
  - `request.queryParameter("filters", filter)`
- Never use non-ConnId pseudo syntax such as:
  - `supportedFilter("name") {{ ... }}`
  - `operator("=", true)`
  - `filterType = "EQUAL"`
  - `request.queryParameter("filters", filters)` when `filters` is not declared in the same scope
""")

_SEARCH_SYSTEM_PROMPT_ID_RULES = textwrap.dedent("""\

INTENT PROFILE: `id`
- Generate ONLY single-object lookup by unique identifier.
- Prefer a dedicated id endpoint path like `users/{{id}}` when documented.
- If the endpoint path placeholder name differs (e.g., `{{userId}}`), map that exact name in `request.pathParameter("<name>", value)`.
- If no dedicated id path exists, use exact-match `supportedFilter(attribute("<id-attr>").eq().anySingleValue())` with the documented query parameter mapping.
- Never generate id intent using only `objectExtractor` without both `singleResult()` and `supportedFilter(...)`.
- Never output leading `/` in `endpoint("...")` for REST search.
- Never use `supportedFilter("id")`, `operator(...)`, or `filterType = ...` in id intent output.
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

get_search_user_prompt = (
    textwrap.dedent("""\
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
""")
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Here are docs where you have to find additional information:

<docs>
{chunk}
</docs>

Result from previous docs:

<result>
{result}
</result>
""")
)
