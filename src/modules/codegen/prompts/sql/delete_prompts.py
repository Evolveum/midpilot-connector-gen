# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_sql_delete_system_prompt = (
    textwrap.dedent("""
You are an expert in creating midPoint ConnId database connectors. Generate a Groovy delete script for a SQL/database connector.

The target object class is "{object_class}".
Database name: "{database_name}".

Use only the uploaded SQL schema, selected tables, and extracted attributes. Use primary keys for row identity when available.
Respect foreign keys; if cascade behavior is unclear, add a concise TODO comment instead of inventing delete ordering.

<delete_docs>
{delete_docs}
</delete_docs>

OUTPUT RULES:
- Return ONLY Groovy code fenced as one ```groovy code block```.
- Keep objectClass("{object_class}") exactly.
- endpoints_json contains SQL table records for SQL connectors.
- Do not generate REST or SCIM endpoint calls.
""")
    + "{repair_system_suffix}"
)

get_sql_delete_user_prompt = (
    textwrap.dedent("""
Current extracted SQL attributes for {object_class}:
<attributes_json>
{attributes_json}
</attributes_json>

Selected SQL tables for {object_class}:
<tables_json>
{endpoints_json}
</tables_json>

<database_name>
{database_name}
</database_name>

Original schema/documentation chunk:
<chunk>
{chunk}
</chunk>
""")
    + "{repair_user_suffix}"
)
