# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap


def build_native_schema_system_prompt(protocol_context_rules: str = "") -> str:
    """Build a protocol-neutral native-schema prompt with optional protocol context."""
    return (
        textwrap.dedent("""
You are an expert in creating connectors for midPoint. Your goal is to prepare a native schema in Groovy code. 
You receive structured schema data extracted in the previous step. It represents one object class ({object_class})
and its attributes from endpoint `api/v1/digester/{{session_id}}/attributes`.
Prepare a native schema in Groovy code based on the following `.adoc` documentations:

<user_schema_docs>
{user_schema_docs}
</user_schema_docs>

ATTRIBUTE NAMING:
- The second argument of `connIdAttribute` and the first argument of `attribute(...)` MUST exactly match the
  native connector attribute name from `name` in <extracted_info>. Never substitute a protocol-level or wire
  name for it.
- Every generated script of one object class must use the identical native name for the same attribute; the
  ConnID connector merges them into one and rejects a mismatch.
""")
        + protocol_context_rules
        + "{repair_system_suffix}"
        + textwrap.dedent("""

OUTPUT RULES:
- Generate only the native schema for the target object class.
- Return ONLY Groovy code, fenced as a single ```groovy code block```. No text outside the code block. 
- Check the example in <user_schema_docs></user_schema_docs>.
- The Groovy structure may vary, but should be consistent and syntactically valid.
""")
    )


def build_native_schema_user_prompt(protocol_context_section: str = "") -> str:
    """Build a protocol-neutral native-schema user prompt with optional protocol context."""
    return (
        textwrap.dedent("""
Here is extracted schema data wrapped into JSON for {object_class}:

<extracted_info>
{records_json}
</extracted_info>
""")
        + protocol_context_section
        + "{repair_user_suffix}"
    )


get_native_schema_system_prompt = build_native_schema_system_prompt()
get_native_schema_user_prompt = build_native_schema_user_prompt()
