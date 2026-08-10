# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.sql.shared_context_prompts import (
    SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_SYSTEM_RULES,
    SQL_SCHEMA_CONTEXT_USER_SECTION,
)

get_sql_create_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating midPoint ConnId database connectors.
Your goal is to prepare a `create` schema in Groovy for a SQL/database connector.

The input data you will receive:
1. The columns extracted for {object_class} in the previous step, each carrying its table and column.
2. A chunk of the original schema or provider documentation.
3. Groovy output from previous chunks that you may minimally complete or edit.

Prepare valid Groovy create code based on the following generic SQL `.adoc` documentation:

<create_docs>
{create_docs}
</create_docs>
""")
    + SQL_SCHEMA_CONTEXT_SYSTEM_RULES
    + SQL_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

OUTPUT RULES:
- <create_docs> is the authoritative source for Groovy DSL structure. The schema context and the
  documentation chunk supply target-specific facts only; they must not replace that structure.
- The output is native SQL DSL, never REST or SCIM DSL. Place the native create operation directly below
  the object class:
  `objectClass("{object_class}") {{ create {{ sql {{ builtIn {{ enabled true }} }} }} }}`.
- The target object class is "{object_class}". Keep `objectClass("{object_class}")` exactly.
- The framework builds the insert from the native schema mapping, so declare `enabled true` and do not
  write column lists, statements or value bindings into the operation block.
- Columns whose extracted `creatable` flag is false, and columns the schema marks as generated, identity
  or serial, are not written on create. If the built-in behavior cannot honor that, add one TODO comment.
- If a mandatory column has no documented default and is not creatable, keep the block and add one TODO
  comment naming that column - never invent a value for it.
- Return ONLY valid Groovy code, fenced as a single ```groovy code block```, with no text outside it.
- No extra commentary.
""")
)

get_sql_create_user_prompt = (
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
