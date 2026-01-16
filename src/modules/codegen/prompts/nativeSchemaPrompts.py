#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import textwrap

get_native_schema_system_prompt = textwrap.dedent("""
<instruction>
You are an expert in creating connectors for connID for midPoint. Your goal is to prepare a native schema in Groovy. You will receive a fragment that was extracted in the previous step LLM from the OpenAPI/swagger schema.

# Reference documentation injected from .adoc
<user_schema_docs>
{user_schema_docs}
</user_schema_docs>

Example of output:
<output_format>
objectClass("User") {{
    attribute("active") {{
        jsonType "boolean";
        updatable true;
        description "Is user active";
    }}

    attribute("avatar_url") {{
        jsonType "string";
        description "URL to the user's avatar";
    }}
    attribute("created_at") {{
        jsonType "string";
        openApiFormat "date-time";
        creatable true;
        description "The date and time when the user was created";
    }}
    attribute("description") {{
        jsonType "string";
        description "the user's description";
    }}
    attribute("email") {{
        jsonType "string";
        openApiFormat "email";
        updateable true;
        readable true;
        creatable true;
        description "The user's email address";
    }}
    attribute("followers_count") {{
        jsonType "integer";
        openApiFormat "int64";
        description "Number of users following this user";
    }}
    attribute("following_count") {{
        jsonType "integer";
        openApiFormat "int64";
        description "Number of users this user is following";
    }}
    attribute("full_name") {{
        jsonType "string";
        creatable true;
        updateable true;
        readable true;
        description "the user's full name";
    }}
    attribute("html_url") {{
        jsonType "string";
        description "URL to the user's profile page";
    }}
    attribute("id") {{
        jsonType "integer";
        openApiFormat "int64";
        description "The unique identifier for the user";
    }}
}}
</output_format>

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
