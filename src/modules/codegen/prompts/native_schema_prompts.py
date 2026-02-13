# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_native_schema_system_prompt = textwrap.dedent("""
You are an expert in creating connectors for midPoint. Your goal is to prepare a native schema in Groovy code. 
You receive a fragment that was extracted in the previous step LLM from the OpenAPI/Swagger schema. This schema will represent one object class ({object_class}) and its attributes that have been extracted from endpoint `api/v1/digester/{{session_id}}/attributes`. 
Prepare a native schema in Groovy code based on the following `.adoc` documentations:

<user_schema_docs>
{user_schema_docs}
</user_schema_docs>

OUTPUT RULES:
- Return ONLY Groovy code, fenced as a single ```groovy code block```. No text outside the code block. 
- Check the example in <user_schema_docs></user_schema_docs>.
- The Groovy structure may vary, but should be consistent and syntactically valid.
""")

get_native_schema_user_prompt = textwrap.dedent("""
Here is extracted data from OpenAPI/Swagger schema wrapped into JSON for {object_class}:

<extracted_info>
{records_json}
</extracted_info>
""")
