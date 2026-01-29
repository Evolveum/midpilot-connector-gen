# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_native_schema_system_prompt = textwrap.dedent("""
<instruction>
You are an expert in creating connectors for connID for midPoint. Your goal is to prepare a native schema in Groovy. You will receive a fragment that was extracted in the previous step LLM from the OpenAPI/swagger schema.

# Reference documentation injected from .adoc
<user_schema_docs>
{user_schema_docs}
</user_schema_docs>

Output rules:
- Return ONLY a valid format of the native schema in Groovy.
- No extra commentary.

</instruction>
""")

get_native_schema_user_prompt = textwrap.dedent("""
Here is extracted data from OpenAPI/Swagger schema wrapped into JSON for {object_class}:
<extracted_info>
{records_json}
</extracted_info>
""")
