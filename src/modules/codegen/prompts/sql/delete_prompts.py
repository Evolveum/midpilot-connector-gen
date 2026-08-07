# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

get_sql_delete_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating midPoint ConnId database connectors.
Your goal is to prepare a `delete` schema in Groovy for a SQL/database connector.

The input data you will receive:
1. The columns extracted for {object_class} in the previous step, each carrying its table and column.
2. A chunk of the original schema or provider documentation.
3. Groovy output from previous chunks that you may minimally complete or edit.

Prepare valid Groovy delete code based on the following generic SQL `.adoc` documentation:

<delete_docs>
{delete_docs}
</delete_docs>
""")
    + SQL_SCHEMA_CONTEXT_SYSTEM_RULES
    + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- <delete_docs> is the authoritative source for Groovy DSL structure. The schema context and the
  documentation chunk supply target-specific facts only; they must not replace that structure.
- The output is native SQL DSL, never REST or SCIM DSL. Place the native delete operation directly below
  the object class:
  `objectClass("{object_class}") {{ delete {{ sql {{ builtIn {{ enabled true }} }} }} }}`.
- The target object class is "{object_class}". Keep `objectClass("{object_class}")` exactly.
- The framework deletes the row identified by the primary key, so declare `enabled true` and do not write
  statements, predicates or cascade handling into the operation block.
- Never invent cascade deletes. When <chunk> shows a foreign key whose cascade behavior is not documented,
  keep the block and add one TODO comment naming the dependency.
- When the schema or the documentation shows a soft-delete column, do not switch the operation to an
  update; keep the block and add one TODO comment stating that deactivation may be required instead.
- If no attribute is marked `primaryKey`, keep the block and add one TODO comment stating that the
  identity column has to be confirmed - never guess it from a column name.
- Return ONLY valid Groovy code, fenced as a single ```groovy code block```, with no text outside it.
- No extra commentary.
""")
)

get_sql_delete_user_prompt = (
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
