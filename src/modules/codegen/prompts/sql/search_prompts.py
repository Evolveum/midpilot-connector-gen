# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap


def _system_prompt(intent: str) -> str:
    return (
        textwrap.dedent(f"""
You are an expert in creating midPoint ConnId database connectors. Generate a Groovy search script for a SQL/database connector.

The target object class is "{{object_class}}". The search intent is {intent}.
Database name: "{{database_name}}".

Use the uploaded SQL schema context and the selected tables. Do not invent tables, columns, joins, filters, or identifiers.
Prefer explicit primary keys and foreign keys from the extracted table metadata. If required information is missing, add a concise TODO comment inside the Groovy code.

<search_docs>
{{search_docs}}
</search_docs>

OUTPUT RULES:
- Return ONLY Groovy code fenced as one ```groovy code block```.
- Keep objectClass("{{object_class}}") exactly.
- Use table/column names from <tables_json> and <attributes_json>; endpoints_json contains table records for SQL.
- Do not generate REST or SCIM endpoint calls.
""")
        + "{repair_system_suffix}"
    )


get_sql_search_all_system_prompt = _system_prompt("all")
get_sql_search_filter_system_prompt = _system_prompt("filter")
get_sql_search_id_system_prompt = _system_prompt("id")

get_sql_search_user_prompt = (
    textwrap.dedent("""
Current extracted SQL attributes for {object_class}:
<attributes_json>
{attributes_json}
</attributes_json>

Selected SQL tables for {object_class}:
<tables_json>
{endpoints_json}
</tables_json>

Optional user-preferred table hints:
<preferred_endpoints>
{preferred_endpoints_json}
</preferred_endpoints>

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
