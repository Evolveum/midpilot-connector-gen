# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Shared prompt fragments for SQL/database operation generation.

A database connector has no operation surface to extract: the table and column mapping is
declared once by the native schema, and every extracted attribute already carries the ``table``
and ``column`` it maps to. Generation therefore works from the attributes alone and declares
capabilities inside a native ``sql {}`` block. These fragments carry that contract once so the
create/update/delete/search prompt families stay consistent.
"""

import textwrap

SQL_PHYSICAL_PROJECTION_SYSTEM_RULES = textwrap.dedent("""\

SQL PHYSICAL SCHEMA VS CONNID PROJECTION:
- <physical_sql_table> is authoritative for the physical table identity. The physical attribute records supplied
  alongside it are authoritative for database columns and SQL/JDBC types.
- <connid_object_class_projection> is authoritative for desired ConnID attribute names and flags. A projection
  name is a logical ConnID name, not a physical database column; never create or rename a physical column from it.
- A non-null projection `column` is the explicit binding to a physical column and must be honored. A projection
  with `column: null` does not describe a database column and must never be presented as one.
- Keep these two abstractions separate. Bind a projection to a physical attribute only when the mapping is
  explicit or supported by the physical schema; never merge `__NAME__`, aliases or other projection-only entries
  into the physical attribute set.
""")

SQL_PHYSICAL_PROJECTION_USER_SECTION = textwrap.dedent("""\

Physical SQL table identity:

<physical_sql_table>
{sql_physical_table_json}
</physical_sql_table>

Separate ConnID object-class projection from Conndev (desired names and flags only):

<connid_object_class_projection>
{sql_connector_object_class_json}
</connid_object_class_projection>

""")

SQL_SCHEMA_CONTEXT_SYSTEM_RULES = textwrap.dedent("""\

SQL SCHEMA CONTEXT RULES:
- <extracted_attributes> is the complete schema context. Every attribute carries the `table` and
  `column` it maps to, its type, and the resolved `mandatory`, `creatable` and `updatable` flags.
  `databaseCatalog` and `databaseSchema` qualify the table when the source supplies them. Treat
  catalog, schema and table as one physical identity and never drop a supplied qualifier. There is
  no separate endpoint or table listing, and none is needed.
- Treat those flags as already resolved. They account for generated, identity and primary key
  columns, so do not re-derive writability from a column name or type.
- A conndev object-class export alone states no primary key, foreign key or nullability. A paired
  SQL-table export can enrich each attribute with `primaryKey` and an exact `foreignKey` target
  (`referencedTable`, `referencedColumn`) plus an optional `constraintName` when the source supplies
  it. Use those values when present, but never infer a key, relationship or constraint name from a
  column name - leave a TODO instead.
- Never invent tables, columns, joins, constraints or identifiers that are absent from
  <extracted_attributes>. When required information is missing, add one concise TODO comment
  inside the Groovy code.
- <database_name> names the database the connector connects to. It is connection configuration,
  never part of a generated statement or identifier.
""")

SQL_SCHEMA_CONTEXT_USER_SECTION = textwrap.dedent("""\

Target database:

<database_name>
{database_name}
</database_name>
""")

SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES = textwrap.dedent("""\

SQL VS REST DSL BOUNDARY:
- The generic SQL operation documentation embedded in this system prompt is authoritative for Groovy DSL
  structure. The extracted attributes and the provider documentation supply target-specific facts only.
- Generate native SQL operation blocks directly below `objectClass(...)`, each holding a `sql {{ ... }}`
  block. Never generate `endpoint(...)`, `httpOperation`, `request {{ ... }}`, response extractors, query
  parameters, request bodies, or any other REST or SCIM construct.
- The table binding is framework-owned metadata declared by the native schema. Table names must never be
  rendered as a path, an endpoint, or a hand-written SQL statement in the operation block.
- The framework owns statement construction, identity resolution from the primary key, type conversion and
  result mapping. Declare capabilities; do not implement them.
- Inside `sql {{ builtIn {{ ... }} }}` only these keys are valid: `enabled`, `emptyFilterSupported` and
  `anyFilterSupported`. Never invent other keys, and never nest anything else inside `builtIn`.
- `attributeResolver {{ ... }}` and `custom {{ ... }}` blocks follow the same shape as REST and SCIM. Generate
  them only when <chunk> or <extracted_attributes> proves the built-in behavior is insufficient.
- Treat <result> as current working code. Preserve already-correct native SQL blocks across chunks and edit
  them minimally; never rewrite them as REST endpoints.
""")
