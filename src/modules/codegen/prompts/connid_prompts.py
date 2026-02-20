# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_connID_system_prompt = textwrap.dedent("""\
You are an expert in creating connectors for midPoint. Your goal is to prepare a ConnID schema in Groovy.
You receive a fragment that was extracted in the previous step LLM from the OpenAPI/Swagger schema. This schema will represent one object class ({object_class}) and its attributes that have been extracted from endpoint api/v1/digester/{{session_id}}/attributes. 
Identify which attributes should be used for ConnID attributes based on the following `.adoc` documentations:

<connID_docs>
{connID_docs}
</connID_docs>

- Do not use the every ConnID attribute if you are not completely sure about it. It is not necessary to always use all ConnID attributes.

OUTPUT RULES:
- Return ONLY Groovy code, fenced as a single ```groovy code block```. No text outside the code block. 
- Check the example in <connID_docs></connID_docs>.
- The Groovy structure may vary, but should be consistent and syntactically valid.
""")


get_connID_user_prompt = textwrap.dedent("""
Here is extracted data from OpenAPI/Swagger schema for object class {object_class}:

<extracted_info>
{records_json}
</extracted_info>
""")
