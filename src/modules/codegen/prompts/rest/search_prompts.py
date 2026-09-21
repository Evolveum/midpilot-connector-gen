# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import (
    REST_ENDPOINT_RULES,
    REST_OPERATION_CONTEXT,
    build_operation_system_prompt,
    build_operation_user_prompt,
)

_COMMON = build_operation_system_prompt(
    "search",
    context_rules=REST_ENDPOINT_RULES,
    rules=r"""
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- Ignore parameters, examples, supported filter lists, and filter payload formats from unrelated endpoints in the same chunk.
- Do not treat <extracted_attributes> as proof that an attribute can be used in a filter; extracted attributes only constrain names/types after the endpoint's own documentation proves filter support.
- Never generate `sortingSupport {{ ... }}` blocks and never reference `sorting.*`.
- Treat <result> as the current working connector artifact. Extend or minimally edit it, but you may replace conflicting parts.
- Treat concrete code already present in <result> as accumulated evidence from earlier chunks. Preserve existing endpoint blocks, `objectExtractor`, `pagingSupport`, `singleResult`, `emptyFilterSupported`, and executable `supportedFilter(...) {{ ... }}` blocks unless the current chunk gives explicit same-endpoint evidence that they are wrong.
- A current chunk that omits filters, pagination, extraction details, or an endpoint parameter list is not evidence that previously generated code is unsupported. If the current chunk adds no relevant or contradictory evidence, return <result> unchanged.
- Do not fabricate endpoints, parameters, attributes, fields, or DSL objects/methods (for example a
  generic HTTP client or request builder not documented for the hook you are writing). If
  documentation is unclear, add a TODO comment.
- Preserve the outer object-class and search structure when present in <result>.
- No extra commentary outside the fenced code block.

- Use search.endpoints for the documented endpoint handler. For a method/body it cannot express, use the documented search.custom.implementation hook (YAML) or custom Groovy search. Do not add an undocumented search.endpoints[].method or request.body key.
- Before accepting an endpoint's `emptyFilterSupported true` as satisfying a list-all/all-instances
  search intent, verify from that endpoint's own documented behavior that it returns the complete
  object-class population, not a caller-scoped, parent-scoped, or single-page subset. A narrower
  endpoint does not satisfy that intent by itself; compose it with a broader enumeration using the
  search.custom composition pattern instead of presenting the narrower endpoint alone as complete.
- There is no raw HTTP client, request builder, or similar object documented for
  search.custom.implementation. The only way to reach another endpoint or object class from inside
  it is composing an already-implemented object class: `objectClass(name).search()`,
  `search(filter)`, `search(filter, resultHandler)`, plus `filter()`, `resultHandler()`,
  `operationOptions()`, `definition()`, and `attributeFilter(protocolName)`. Never invent a
  different API inside that hook.
""",
)

get_search_all_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: `all`
- Generate ONLY support for listing all objects / empty-filter retrieval.
- Prefer collection endpoints and include pagination handling when documented.
- Declare `emptyFilterSupported true` only inside an `endpoint("...") {{ ... }}` block.
- Do not add dedicated id-lookup or broad attribute filters unless strictly required by docs for list behavior.
"""
)

get_search_filter_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: `filter`
- Generate ONLY filter-based search support.
- A REST filter is documented only when the target endpoint's own method-specific documentation or <extracted_endpoints> explicitly includes a filter-capable request parameter (for example `filter`, `filters`, or an attribute-specific query parameter) and documents the specific filter attribute/operator or gives an example for that same endpoint.
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
  - `String filter = "[{{ \"name\": {{ \"operator\": \"=\", \"values\": [\"${{value}}\"] }} }}]"`
  - `request.queryParameter("filters", filter)`
- Never use non-ConnId pseudo syntax such as:
  - `supportedFilter("name") {{ ... }}`
  - `operator("=", true)`
  - `filterType = "EQUAL"`
  - `request.queryParameter("filters", filters)` when `filters` is not declared in the same scope
"""
)

get_search_id_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: `id`
- Generate ONLY single-object lookup by unique identifier.
- Prefer a dedicated id endpoint path like `users/{{id}}` when documented.
- If the endpoint path placeholder name differs (e.g., `{{userId}}`), map that exact name in `request.pathParameter("<name>", value)`.
- If no dedicated id path exists, use exact-match `supportedFilter(attribute("<id-attr>").eq().anySingleValue())` with the documented query parameter mapping.
- Never generate id intent using only `objectExtractor` without both `singleResult()` and `supportedFilter(...)`.
- Never output leading `/` in `endpoint("...")` for REST search.
- Never use `supportedFilter("id")`, `operator(...)`, or `filterType = ...` in id intent output.
- Do not add list/get-all logic or non-id filters.

- A path-parameter mapping alone does not require a full Groovy artifact. Prefer this YAML shape,
  substituting the documented native identifier and exact placeholder name:
  objectClasses:
    {object_class}:
      search:
        endpoints:
          - path: organizations/{{org}}
            singleResult: true
            supportedFilters:
              - spec: attribute("<native-identifier>").eq().anySingleValue()
                request: |
                  request.pathParameter("org", value)
"""
)

get_search_user_prompt = build_operation_user_prompt(REST_OPERATION_CONTEXT, intent=True)
