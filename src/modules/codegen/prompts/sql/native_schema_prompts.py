# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SQL native-schema prompts: the shared builder plus the physical/projection boundary rules."""

import textwrap

from src.modules.codegen.prompts.native_schema_prompts import (
    build_native_schema_system_prompt,
    build_native_schema_user_prompt,
)

_SQL_NATIVE_SCHEMA_SYSTEM_RULES = textwrap.dedent("""\

SQL PHYSICAL VS PROJECTION RULES:
- The input separates authoritative database facts from the desired ConnId projection. Never merge the two
  abstractions before deciding an explicit mapping.
- <extracted_info> and <physical_sql_table> are authoritative for table, schema, columns, keys, nullability,
  generation, uniqueness, defaults and SQL/JDBC types.
- <connid_object_class_projection> is authoritative only for desired ConnId names and flags. Its `column` is
  evidence only when non-null; a null binding must never be inferred.
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

_SQL_CONTEXT_USER_SECTION = textwrap.dedent("""\

Physical SQL table identity for {object_class}:

<physical_sql_table>
{sql_physical_table_json}
</physical_sql_table>

Separate ConnId object-class projection from Conndev (desired names and flags only):

<connid_object_class_projection>
{sql_connector_object_class_json}
</connid_object_class_projection>
""")

get_sql_native_schema_system_prompt = build_native_schema_system_prompt(_SQL_NATIVE_SCHEMA_SYSTEM_RULES)
get_sql_native_schema_user_prompt = build_native_schema_user_prompt(_SQL_CONTEXT_USER_SECTION)
