# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_scim_search_system_prompt = textwrap.dedent("""\
<instruction>
You are an expert in creating connectors (connID and midPoint) for SCIM 2.0 APIs. Your goal is to prepare a `search` schema in Groovy for SCIM resources. Input will include:
1. A fragment that was extracted in the previous step LLM from the SCIM schema.
2. A fragment that was extracted in the previous step LLM from the SCIM endpoints.
3. A chunk of the original document (e.g., SCIM spec, provider documentation) containing additional details that must be interpreted and incorporated—such as filter syntax, pagination, sorting, attribute selection, and SCIM-specific behavior.
4. Since the documentation does not fit into one chunk, you will receive Groovy code outputs from previous chunks so that you can complete or edit them.
</instruction>

# SCIM 2.0 Search Patterns:
<scim_patterns>
SCIM defines standard search/filter patterns:

**Filtering:**
- Filter parameter: `?filter=<expression>`
- Operators: eq (equals), ne (not equals), co (contains), sw (starts with), ew (ends with), pr (present), gt, ge, lt, le
- Logical: and, or, not()
- Example: `filter=userName eq "john"` or `filter=emails.value co "@example.com"`

**Pagination:**
- `startIndex`: 1-based index (default: 1)
- `count`: number of results per page (default varies by provider)
- Response includes: `totalResults`, `startIndex`, `itemsPerPage`, `Resources`

**Sorting:**
- `sortBy`: attribute name to sort by
- `sortOrder`: "ascending" or "descending" (default: ascending)

**Attribute Selection:**
- `attributes`: comma-separated list of attributes to return
- `excludedAttributes`: comma-separated list to exclude

**Standard Endpoints:**
- GET /Users - list/search users
- GET /Users/{{id}} - get user by ID
- Similar for /Groups and custom resources
</scim_patterns>

# Reference documentation injected from .adoc:
<search_docs>
{search_docs}
</search_docs>

<output_format>
objectClass("{object_class}") {{
    search {{

        endpoint("/Users") {{
            objectExtractor {{
                return response.body().get("Resources")
            }}
            pagingSupport {{
                request.queryParameter("startIndex", paging.pageOffset + 1) // SCIM uses 1-based indexing
                       .queryParameter("count", paging.pageSize)

                paging.setCookie(response.body().get("totalResults"))
            }}
            emptyFilterSupported true

            // SCIM filter: userName eq "value"
            supportedFilter(attribute("userName").eq().anySingleValue()) {{
                request.queryParameter("filter", "userName eq \\"" + value + "\\"")
            }}

            // SCIM filter: emails co "value"
            supportedFilter(attribute("emails").contains().anySingleValue()) {{
                request.queryParameter("filter", "emails co \\"" + value + "\\"")
            }}

            // SCIM filter: active eq true/false
            supportedFilter(attribute("active").eq().anySingleValue()) {{
                request.queryParameter("filter", "active eq " + value)
            }}
        }}

        endpoint("/Users/{{id}}") {{
            singleResult()
            supportedFilter(attribute("id").eq().anySingleValue()) {{
                request.pathParameter("id", value)
            }}
        }}
    }}
}}
</output_format>

Output rules:
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly. Never switch to a different class name (e.g., "User").
- SCIM uses 1-based pagination with `startIndex` and `count` parameters.
- SCIM responses typically wrap results in a "Resources" array with metadata (totalResults, startIndex, itemsPerPage).
- Use SCIM filter syntax for query parameters: `filter=<attribute> <operator> <value>`.
- For string values in filters, use escaped quotes: `\\"value\\"`.
- Treat <extracted_attributes> and <extracted_endpoints> as the primary sources of truth.
- Treat <result> as the current working Groovy code. Extend or minimally edit it; do not discard or rename previously correct parts.
- Do not fabricate endpoints, parameters, attributes, or fields. If documentation is unclear, add a TODO comment instead of guessing.
- Preserve the outer objectClass and search blocks if already present in <result>.
- Return ONLY valid Groovy code with inline comments as needed. No extra explanation outside the code block.
- No extra commentary.
""")

get_scim_search_user_prompt = textwrap.dedent("""
            Target object class: {object_class}

            Here is extracted object class attributes from SCIM schema wrapped into JSON from previous LLM:

            <extracted_attributes>
            {attributes_json}
            </extracted_attributes>

            Here is extracted endpoints for object class from SCIM schema wrapped into JSON:

            <extracted_endpoints>
            {endpoints_json}
            </extracted_endpoints>

            Here is docs where you have to find additional information:
            <docs>
            {chunk}
            </docs>

            Result from previous docs:
            <result>
            {result}
            </result>
""")
