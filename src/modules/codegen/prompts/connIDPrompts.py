#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import textwrap

get_connID_system_prompt = textwrap.dedent("""\
<instruction>
You are an expert in creating connectors (connID and midPoint). Your goal is to prepare a ConnID schema in Groovy.
1. A fragment that was extracted in the previous step LLM from the OpenAPI/Swagger schema. This schema will represent one object class and its attributes that have been extracted.
2. Identify which attributes should be used for ConnID attributes based on the following documentations:

<connID_docs>
{connID_docs}
</connID_docs>


- Do not use the evey ConnID attribute if you are not completely sure about it. It is not necessary to always use all ConnID attributes.
</instruction>

<output_format>
objectClass("User") {{
    /** Mapping from native attributes to connID attributes **/
    connIdAttribute("UID", "id");
    connIdAttribute("NAME", "login");

    /*
    connIdAttribute "ENABLE" "active";
    connIdAttribute "LAST_LOGIN_DATE" "last_login_date";
    connIdAttribute "LOCK_OUT" "prohibit_login";
    connIdAttribute "SHORT_NAME" "full_name";
    connIdAttribute "DESCRIPTION" "description";
    */
}}
</output_format>

Output rules:
- Return ONLY a valid format of the native schema in Groovy, including the inline comments as specified. No extra explanation outside the code block.
- The output format is just an example and may vary slightly based on the various specifications and documentations that will be available to you in the user prompt.
- No extra commentary.

""")


get_connID_user_prompt = textwrap.dedent("""
Here is extracted data from OpenAPI/Swagger schema for object class {object_class}:
<extracted_info>
{records_json}
</extracted_info>
""")
