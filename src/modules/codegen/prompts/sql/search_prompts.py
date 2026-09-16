# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

_COMMON = build_operation_system_prompt(
    "search",
    context_rules=SQL_SCHEMA_CONTEXT_SYSTEM_RULES + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
- Built-in SQL search already translates filters and handles pagination once the native schema maps
  the UID. All three intents use those defaults; emit {{}} when no customization is needed.
- If target evidence requires a fixed WHERE predicate or custom query, generate the complete artifact
  in Groovy using <search_docs>. The SQL YAML reference has no scripted search keys.
- Keep the requested object class name exactly {object_class}; never invent columns, join
  conditions, or SqlCustomQueryBuilderContext methods beyond the documented table, column (with
  eq/ne/asc/desc), select, from, where, orderBy, value, sqlValue, and filter. There is no raw JDBC
  or HTTP client documented for a custom query; if a requirement needs something that builder
  cannot express, say so with a TODO instead of inventing a method.
- Use explicit eq/ne calls or the documented query API to build predicates; Groovy == is not SQL equality.
""",
)

get_sql_search_all_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: all
- Cover empty-filter listing; keep documented permanent row restrictions and rely on built-in pagination.
"""
)

get_sql_search_filter_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: filter
- Use built-in ConnId filter translation for mapped columns. Add customization only for documented requirements beyond it.
"""
)

get_sql_search_id_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: id
- Use the UID mapping supplied by the native schema. Do not guess identity from a column name or declare a filter limitation merely to enable existing UID retrieval.
"""
)

get_sql_search_user_prompt = build_operation_user_prompt(SQL_SCHEMA_CONTEXT_USER_SECTION, intent=True)
