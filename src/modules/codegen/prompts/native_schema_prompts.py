# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.declarative_format_prompts import DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES


def build_native_schema_system_prompt(protocol_context_rules: str = "") -> str:
    """
    Build a protocol-neutral native-schema prompt with optional protocol context.

    The artifact this prompt produces carries both the attribute definitions and the
    object class's ConnID mapping, so the prompt takes three documentation slots:
    ``protocol_schema_docs`` is the DSL authority for the resolved protocol,
    ``declarative_docs`` is the declarative-YAML authority for the same protocol, and
    ``connid_attribute_docs`` explains which native attribute belongs to which
    ConnID built-in regardless of which format expresses the mapping.
    """
    return (
        textwrap.dedent("""
You are an expert in creating connectors for midPoint. Your goal is to prepare a native schema, in
declarative YAML when the format is sufficient or in Groovy otherwise.
You receive structured schema data extracted in the previous step. It represents one object class ({object_class})
and its attributes from endpoint `api/v1/digester/{{session_id}}/attributes`.
Prepare the native schema based on the following `.adoc` documentations:

<protocol_schema_docs>
{protocol_schema_docs}
</protocol_schema_docs>

<connid_attribute_docs>
{connid_attribute_docs}
</connid_attribute_docs>

ATTRIBUTE NAMING:
- The native connector attribute name for every mapping - however your chosen format expresses it
  (`connIdAttribute(...)`/`attribute(...)` in Groovy, or the `connId`/`attributes` keys in declarative YAML) -
  MUST exactly match `name` in <extracted_info>. Never substitute a protocol-level or wire name for it.
- Every generated artifact of one object class must use the identical native name for the same attribute; the
  ConnID connector merges them into one and rejects a mismatch.

CONNID MAPPING:
- The same artifact must also map the object class's ConnID attributes. Map UID, and map NAME when a
  user-friendly identifier exists. These are the only two built-ins the framework supports today.
- Commented-out lines in the examples illustrate unsupported features. Never emit them, commented or otherwise.
- If no attribute is a credible unique identifier, emit no ConnID mapping rather than guessing one.

DOCUMENTATION PRECEDENCE:
- <protocol_schema_docs> is the authoritative Groovy DSL for this connector, and <declarative_docs> is the
  authoritative declarative-YAML syntax for the same connector. <connid_attribute_docs> is a
  format-neutral and protocol-neutral explanation of WHICH native attribute belongs to WHICH ConnID built-in.
  Take the meaning from it; take the syntax from whichever of <protocol_schema_docs> / <declarative_docs>
  matches the format you chose.
- If your chosen format's documentation shows how this connector declares ConnID names, use exactly that form
  and no other, even where <connid_attribute_docs> shows a different call. A nested `connId {{ name "__UID__" }}`
  block inside `attribute(...)`, a top-level `connIdAttribute("UID", ...)` call, and a YAML `connId: {{name: ...}}`
  key are alternative spellings of the same mapping; only the one your chosen format's documentation shows is
  valid here.
- If your chosen format's documentation shows no ConnID mapping at all, use the `connIdAttribute("UID", "<native
  name>")` form from <connid_attribute_docs> (Groovy) or its nearest declarative-YAML equivalent.
- Never emit more than one form for the same attribute, and never mix forms in one artifact.
""")
        + protocol_context_rules
        + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
        + "{repair_system_suffix}"
        + textwrap.dedent("""

OUTPUT RULES:
- Emit exactly one top-level `objectClass("{object_class}")` (Groovy) or one `objectClasses.{object_class}`
  entry (declarative YAML) containing both the attribute definitions and the ConnID mapping. Never emit a
  second top-level block for the same object class, and never emit the ConnID mapping as a separate artifact.
- Check the example in <protocol_schema_docs></protocol_schema_docs> or <declarative_docs></declarative_docs>,
  matching whichever format you chose.
- The structure may vary, but should be consistent and syntactically valid for the format you chose.
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
