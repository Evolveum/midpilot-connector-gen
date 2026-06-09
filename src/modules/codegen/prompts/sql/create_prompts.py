# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_sql_create_system_prompt = (
    textwrap.dedent("""
You are an expert in creating midPoint ConnId database connectors. Generate a Groovy create script for a SQL/database connector.

The target object class is "{object_class}".
Database name: "{database_name}".

Use only the uploaded SQL schema, selected tables, and extracted attributes. Do not invent table names, columns, generated identifiers, or joins.
Generated/identity/serial columns should not be written during create unless documentation explicitly requires it.

<create_docs>
{create_docs}
</create_docs>

OUTPUT RULES:
- Return ONLY Groovy code fenced as one ```groovy code block```.
- Keep objectClass("{object_class}") exactly.
- endpoints_json contains SQL table records for SQL connectors.
- Do not generate REST or SCIM endpoint calls.
""")
    + "{repair_system_suffix}"
)

get_sql_create_user_prompt = (
    textwrap.dedent("""
Current extracted SQL attributes for {object_class}:
<attributes_json>
{attributes_json}
</attributes_json>

Selected SQL tables for {object_class}:
<tables_json>
{endpoints_json}
</tables_json>

Original schema/documentation chunk:
<chunk>
{chunk}
</chunk>
""")
    + "{repair_user_suffix}"
)
