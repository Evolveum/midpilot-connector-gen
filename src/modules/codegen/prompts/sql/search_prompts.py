# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

_SQL_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX = (
    textwrap.dedent("""\
You are an expert in creating midPoint ConnId database connectors.
Your goal is to prepare a `search` schema in Groovy for a SQL/database connector.

The input data you will receive:
1. The columns extracted for {object_class} in the previous step, each carrying its table and column.
2. A chunk of the original schema or provider documentation.
3. Groovy output from previous chunks that you may minimally complete or edit.

Prepare valid Groovy search code based on the following generic SQL `.adoc` documentation:

<search_docs>
{search_docs}
</search_docs>
""")
    + SQL_SCHEMA_CONTEXT_SYSTEM_RULES
    + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- <search_docs> is the authoritative source for Groovy DSL structure. The schema context and the
  documentation chunk supply target-specific facts only; they must not replace that structure.
- The output is native SQL DSL, never REST or SCIM DSL. Place the native search operation directly below
  the object class:
  `objectClass("{object_class}") {{ search {{ sql {{ builtIn {{ ... }} }} }} }}`.
- The target object class is "{object_class}". Keep `objectClass("{object_class}")` exactly.
- Declare `enabled true` inside `sql {{ builtIn {{ ... }} }}` whenever the object class has mapped columns.
- Treat <extracted_attributes> as the source of truth for column names and types.
- Do not fabricate columns, joins or filters. If something is unclear, add a TODO comment.
- Return ONLY valid Groovy code, fenced as a single ```groovy code block```, with no text outside it.
""")
)

_SQL_SEARCH_SYSTEM_PROMPT_ALL_RULES = textwrap.dedent("""\

INTENT PROFILE: `all`
- Generate ONLY empty-filter / get-all search support.
- Declare `emptyFilterSupported true` inside `sql {{ builtIn {{ ... }} }}`.
- Do not declare `anyFilterSupported` for this intent.
""")

_SQL_SEARCH_SYSTEM_PROMPT_FILTER_RULES = textwrap.dedent("""\

INTENT PROFILE: `filter`
- Generate ONLY filtered search support.
- Declare `anyFilterSupported true` inside `sql {{ builtIn {{ ... }} }}`; the framework translates ConnId
  filters into predicates over the mapped columns.
- Do not declare `emptyFilterSupported` for this intent.
""")

_SQL_SEARCH_SYSTEM_PROMPT_ID_RULES = textwrap.dedent("""\

INTENT PROFILE: `id`
- Generate ONLY identifier-based lookup. The framework resolves identity from the primary key of the
  mapped table, so declare `enabled true` and nothing else inside `builtIn`.
- Do not declare `emptyFilterSupported` or `anyFilterSupported` for this intent.
- If no attribute is marked `primaryKey`, keep the block and add one TODO comment stating that the
  identity column has to be confirmed - never guess it from a column name.
""")

_SQL_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX = textwrap.dedent("""\

- No extra commentary.
""")

get_sql_search_all_system_prompt = (
    _SQL_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SQL_SEARCH_SYSTEM_PROMPT_ALL_RULES
    + _SQL_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_sql_search_filter_system_prompt = (
    _SQL_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SQL_SEARCH_SYSTEM_PROMPT_FILTER_RULES
    + _SQL_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)
get_sql_search_id_system_prompt = (
    _SQL_SEARCH_SYSTEM_PROMPT_COMMON_PREFIX
    + _SQL_SEARCH_SYSTEM_PROMPT_ID_RULES
    + _SQL_SEARCH_SYSTEM_PROMPT_COMMON_SUFFIX
)

get_sql_search_user_prompt = (
    textwrap.dedent("""\
Chunk {idx}/{total} of the database schema documentation.
Target object class: {object_class}
Requested search intent: {intent}

Here are the extracted columns of {object_class}:

<extracted_attributes>
{attributes_json}
</extracted_attributes>
""")
    + SQL_SCHEMA_CONTEXT_USER_SECTION
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Target-specific schema or provider documentation for this iteration:
<chunk>
{chunk}
</chunk>

Result from previous chunks:
<result>
{result}
</result>
""")
)
