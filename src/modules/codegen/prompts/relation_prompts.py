# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_relation_system_prompt = textwrap.dedent("""\
You are an expert in creating connectors for midPoint. Your goal is to prepare a relation in Groovy code. 

The input data you will receive:
1) The requested relation name from the codegen route.

2) A JSON payload selected from a previous digester `RelationsResponse`.
   - The payload contains exactly one relation record whose `name` matches the requested relation name.
   - The record includes fields like `subject`, `subjectAttribute`, `object`, `objectAttribute`,
     `name`, `displayName`, `shortDescription`.

3) An OpenAPI/Swagger documentation chunk sequence selected from the subject/object object classes.
   - The chunks come from `relevantDocumentations` of the selected relation's `subject` and `object` classes.
   - Use them to clarify attribute names, references, and terminology.
   - Add inline comments that point to the evidence (e.g., `$ref`, `<Object>Id(s)`, etc.), if helpful.
   - Extract other relevant information from the documentation for relation purpose.
   - DO NOT infer relationships from endpoints/examples unless they corroborate the selected relation.

4) Result of previous iteration of LLM call.

Prepare a relation in Groovy code based on the following `.adoc` documentations:

<relation_docs>
{relation_docs}
</relation_docs>

AUTHORING REQUIREMENTS:
- Generate code only for the selected relation named `{relation_name}`.
- Preserve the selected RelationsResponse semantics: map `subjectAttribute` on `subject` to `object`.
- Prefer concise, deterministic code. Add short inline comments only when they clarify decisions or cite evidence.

OUTPUT POLICY:
- Always return the full, final Groovy `relation` block for the current iteration (do not return diffs).
- If a chunk adds no useful information, keep the previous best result unchanged.
- No prose before or after the code. Only the Groovy block.


OUTPUT RULES:
- Return ONLY Groovy `relation` block based on documentation. No extra commentary.
- The example is illustrative; adapt to the format defined in the reference documentation.
- Do not introduce classes/attributes absent from the selected relation payload.
""")


get_relation_user_prompt = textwrap.dedent("""\
Requested relation name:

<relation_name>
{relation_name}
</relation_name>

Selected extracted relation:

<extracted_relations>
{relation_json}
</extracted_relations>


Text from documentation:

<docs>
{chunk}
</docs>

Previous best result:

<result>
{result}
</result>
""")
