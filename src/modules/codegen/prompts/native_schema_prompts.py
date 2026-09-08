# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap


def build_native_schema_system_prompt(protocol_context_rules: str = "") -> str:
    """
    Build a protocol-neutral native-schema prompt with optional protocol context.

    The script this prompt produces carries both the attribute definitions and the
    object class's ConnID mapping, so the prompt takes two documentation slots:
    ``protocol_schema_docs`` is the DSL authority for the resolved protocol, and
    ``connid_attribute_docs`` explains which native attribute belongs to which
    ConnID built-in. The precedence between them is stated explicitly because the
    two references describe the same mapping in different syntaxes.
    """
    return (
        textwrap.dedent("""
You are an expert in creating connectors for midPoint. Your goal is to prepare a native schema in Groovy code.
You receive structured schema data extracted in the previous step. It represents one object class ({object_class})
and its attributes from endpoint `api/v1/digester/{{session_id}}/attributes`.
Prepare a native schema in Groovy code based on the following `.adoc` documentations:

<protocol_schema_docs>
{protocol_schema_docs}
</protocol_schema_docs>

<connid_attribute_docs>
{connid_attribute_docs}
</connid_attribute_docs>

ATTRIBUTE NAMING:
- The second argument of `connIdAttribute` and the first argument of `attribute(...)` MUST exactly match the
  native connector attribute name from `name` in <extracted_info>. Never substitute a protocol-level or wire
  name for it.
- Every generated script of one object class must use the identical native name for the same attribute; the
  ConnID connector merges them into one and rejects a mismatch.

CONNID MAPPING:
- The same script must also map the object class's ConnID attributes. Map UID, and map NAME when a
  user-friendly identifier exists. These are the only two built-ins the framework supports today.
- Commented-out lines in the examples illustrate unsupported features. Never emit them, commented or otherwise.
- If no attribute is a credible unique identifier, emit no ConnID mapping rather than guessing one.

DOCUMENTATION PRECEDENCE:
- <protocol_schema_docs> is the authoritative DSL for this connector. <connid_attribute_docs> is a
  protocol-neutral explanation of WHICH native attribute belongs to WHICH ConnID built-in. Take the meaning
  from it; take the syntax from <protocol_schema_docs>.
- If <protocol_schema_docs> shows how this connector declares ConnID names, use exactly that form and no
  other, even where <connid_attribute_docs> shows a different call. A nested `connId {{ name "__UID__" }}`
  block inside `attribute(...)` and a top-level `connIdAttribute("UID", ...)` call are alternative spellings
  of the same mapping; only the one your protocol documentation shows is valid here.
- If <protocol_schema_docs> shows no ConnID mapping at all, use the `connIdAttribute("UID", "<native name>")`
  form from <connid_attribute_docs>.
- Never emit both forms for the same attribute, and never mix the two forms in one script.
""")
        + protocol_context_rules
        + "{repair_system_suffix}"
        + textwrap.dedent("""

OUTPUT RULES:
- Emit exactly one top-level `objectClass("{object_class}")` block containing both the attribute definitions
  and the ConnID mapping. Never emit a second objectClass block, and never emit the ConnID mapping as a
  separate script.
- Return ONLY Groovy code, fenced as a single ```groovy code block```. No text outside the code block.
- Check the example in <protocol_schema_docs></protocol_schema_docs>.
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
