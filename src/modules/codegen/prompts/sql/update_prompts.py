# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.declarative_format_prompts import DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

get_sql_update_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating midPoint ConnId database connectors.
Your goal is to prepare an `update` schema for a SQL/database connector, in declarative YAML when the
format is sufficient or in Groovy otherwise.

The input data you will receive:
1. The columns extracted for {object_class} in the previous step, each carrying its table and column.
2. A chunk of the original schema or provider documentation.
3. Prior output from previous chunks that you may minimally complete or edit, in whichever format you chose.

Prepare the update schema based on the following generic SQL `.adoc` documentation:

<update_docs>
{update_docs}
</update_docs>
""")
    + SQL_SCHEMA_CONTEXT_SYSTEM_RULES
    + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- <update_docs> is the authoritative source for Groovy DSL structure, and <declarative_docs> for its
  declarative-YAML equivalent. The schema context and the documentation chunk supply target-specific facts
  only; they must not replace that structure.
- The output is native SQL, never REST or SCIM DSL. In Groovy, place the native update operation directly
  below the object class: `objectClass("{object_class}") {{ update {{ sql {{ builtIn {{ enabled true }} }} }} }}`.
  In declarative YAML, the equivalent is `objectClasses.{object_class}.update: {{enabled: true}}` - and since
  update is enabled by default, an object class needing no update customization needs no `update` key at all.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in
  declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- The framework identifies the row by the primary key and writes only the changed attributes, so do not
  write statements, predicates or value bindings into the update configuration; only declare
  `enabled`/`enabled: false` when narrowing default behavior, per <update_docs>/<declarative_docs>.
- Columns whose extracted `updatable` flag is false, primary key columns, and generated columns are not
  written on update. If the built-in behavior cannot honor that, add one TODO comment.
- If no attribute is marked `primaryKey`, add one TODO comment stating that the identity column has to be
  confirmed - never guess it from a column name.
- No extra commentary outside the fenced code block.
""")
)

get_sql_update_user_prompt = (
    textwrap.dedent("""\
Chunk {idx}/{total} of the database schema documentation.
Target object class: {object_class}

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
