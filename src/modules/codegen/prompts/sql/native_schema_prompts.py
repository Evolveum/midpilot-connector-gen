# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SQL native-schema prompts: the shared builder plus the physical/projection boundary rules."""

import textwrap

from src.modules.codegen.prompts.native_schema_prompts import (
    build_native_schema_system_prompt,
    build_native_schema_user_prompt,
)
from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_PHYSICAL_PROJECTION_SYSTEM_RULES,
    SQL_PHYSICAL_PROJECTION_USER_SECTION,
)

_SQL_NATIVE_SCHEMA_SYSTEM_RULES = textwrap.dedent("""\

SQL NATIVE-SCHEMA MAPPING RULES:
- The first argument of `attribute("...")` is always a physical column from <extracted_info>. Put every ConnId
  target, including `__NAME__`, in that physical attribute's nested `connId {{ name "..." }}` block.
- Honor an explicit projection `column` when it names a physical column. Without one, decide credible renames and
  system mappings from names, primary/unique keys, flags and both references. Omit an unresolved projection
  mapping; never create an attribute without a physical column.
- Never derive an SQL type from `connIdType`. Use `databaseType` when an explicit SQL type override is useful, or
  omit the SQL type so JDBC discovery supplies it.
- Declare the exact table and schema from <physical_sql_table>. Do not invent or transform either identity.
- Use `onlyExplicitlyListed true` only when every physical column is declared in the script.
""")

get_sql_native_schema_system_prompt = build_native_schema_system_prompt(
    SQL_PHYSICAL_PROJECTION_SYSTEM_RULES + _SQL_NATIVE_SCHEMA_SYSTEM_RULES
)
get_sql_native_schema_user_prompt = build_native_schema_user_prompt(SQL_PHYSICAL_PROJECTION_USER_SECTION)
