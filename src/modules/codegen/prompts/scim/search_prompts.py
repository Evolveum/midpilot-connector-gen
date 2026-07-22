# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

_SCIM_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX = (
    textwrap.dedent("""\
You are an expert in creating connectors (connID and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare a `search` schema in Groovy for SCIM resources.

The input data you will receive:
1. A fragment that was extracted in the previous step LLM from SCIM attributes for {object_class}.
2. A chunk of the original provider documentation containing target-specific capabilities and constraints.
3. Groovy output from previous chunks that you may minimally complete or edit.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Prepare valid Groovy search schema code based on the following generic SCIM `.adoc` documentation:

<search_docs>
{search_docs}
</search_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- <search_docs> is the authoritative source for Groovy DSL structure. The provider chunk and extracted SCIM contracts
  supply target-specific facts only; they must not replace that structure with REST DSL examples.
- The output is native SCIM DSL, never REST DSL. Follow <search_docs> and place the native search operation directly
  below the object class:
  `objectClass("{object_class}") {{ search {{ scim {{ limitations {{ ... }} }} }} }}`.
- Never generate `endpoint(...)` anywhere in SCIM output. Resource endpoints and URLs in the supplied context are
  framework-owned metadata and must not be rendered into Groovy.
- Keep `emptyFilterSupported`, `anyFilterSupported`, and declarative `supportedFilter attribute(...)...` statements
  inside `scim {{ limitations {{ ... }} }}` exactly as defined by <search_docs>.
- Never replace the `scim {{ limitations {{ ... }} }}` block with a REST implementation. Do not generate
  `request {{ ... }}`, `request.queryParameter(...)`,
  `objectExtractor`, `pagingSupport`, or `singleResult()` for native SCIM search. The SCIM framework handles filter
  serialization, response extraction, pagination, and single-object semantics.
- The target object class is "{object_class}". You must keep objectClass("{object_class}") exactly.
- Treat <extracted_attributes> as the primary source of truth for target attribute names and types.
- Never generate `sortingSupport {{ ... }}` blocks and never reference `sorting.*`.
- Express filtering support declaratively with the SCIM `supportedFilter attribute(...)...` DSL when
  `filter.supported` is true in <scim_service_provider_config>. Never generate it when that flag is explicitly false.
  When <scim_service_provider_config> is empty or omits the filter capability, generate filtering only when <chunk>
  explicitly proves it.
- Treat <result> as current working code and minimally edit or extend it.
- Do not fabricate parameters, attributes, or fields. If unclear, add a TODO comment.
- Preserve existing correct `objectClass`, `search`, `scim`, and `limitations` blocks in <result> across chunks.
- Return ONLY valid Groovy code, with no explanation outside the code.
""")
)

_SCIM_SEARCH_SYSTEM_PROMPT_ALL_RULES = textwrap.dedent("""\

INTENT PROFILE: `all`
- Generate ONLY empty-filter / get-all search support.
- Declare `emptyFilterSupported true` under `scim {{ limitations {{ ... }} }}` when supported.
- Rely on the native SCIM framework for resource routing and pagination behavior; do not encode them as REST requests.
- Do not add broad supported filters unless <chunk> requires them for list behavior.
""")

_SCIM_SEARCH_SYSTEM_PROMPT_FILTER_RULES = textwrap.dedent("""\

INTENT PROFILE: `filter`
- Generate ONLY documented SCIM filtering capabilities. An explicit `filter.supported: false` disables this intent;
  `true` enables documented filtering. If the capability contract is empty or omits that flag, filtering is allowed
  only when <chunk> explicitly documents it.
- Prefer declarative `supportedFilter attribute(...)...` or `anyFilterSupported true` inside
  `scim {{ limitations {{ ... }} }}` based on <search_docs>.
- Do not add get-all behavior unless <chunk> explicitly shows it is part of filtered mode.
""")

_SCIM_SEARCH_SYSTEM_PROMPT_ID_RULES = textwrap.dedent("""\

INTENT PROFILE: `id`
- Generate ONLY identifier-based lookup.
- Declare an exact-match SCIM filter for the documented unique identifier, such as
  `supportedFilter attribute("id").eq().anySingleValue()`, inside `scim {{ limitations {{ ... }} }}`.
- Do not generate an item endpoint, `singleResult()`, path-parameter mapping, or a manual SCIM query parameter.
- Do not add list/get-all logic or non-id filters.
""")

_SCIM_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX = textwrap.dedent("""\

- No extra commentary.
""")

get_scim_search_all_system_prompt = (
    _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SCIM_SEARCH_SYSTEM_PROMPT_ALL_RULES
    + _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_scim_search_filter_system_prompt = (
    _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SCIM_SEARCH_SYSTEM_PROMPT_FILTER_RULES
    + _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_scim_search_id_system_prompt = (
    _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SCIM_SEARCH_SYSTEM_PROMPT_ID_RULES
    + _SCIM_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)

get_scim_search_user_prompt = (
    textwrap.dedent("""\
Chunk {idx}/{total} of the SCIM schema:
Target object class: {object_class}
Requested search intent: {intent}

Here are the extracted object-class attributes from the SCIM schema:

<extracted_attributes>
{attributes_json}
</extracted_attributes>
""")
    + SCIM_CONTRACT_CONTEXT_USER_SECTION
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Target-specific provider documentation for this iteration:
<chunk>
{chunk}
</chunk>

Result from previous chunks:
<result>
{result}
</result>
""")
)
