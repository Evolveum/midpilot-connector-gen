#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

import textwrap

get_relation_system_prompt = textwrap.dedent("""\
<instruction>
You are an expert in IGA connector code generation in Groovy language.
Your goal: produce a valid Groovy block that captures relationships between object classes.

Input you will receive:
1) A JSON payload produced by a previous step (`/digester/getRelation`) representing RelationsResponse:
   - Each record includes fields like `subject`, `subjectAttribute`, `object`, and possibly `objectAttribute`, `name`,`shortDescription`.

2) An OpenAPI/Swagger documentation chunk sequence (the original source). Use it for:
   - Clarifying attribute names, references, and terminology.
   - Adding inline comments that point to the evidence (e.g., `$ref`, `<Object>Id(s)`, etc.), if helpful.
   - Extract other relevant information from the documentation for relation purpose
   - DO NOT infer relationships from endpoints/examples unless they corroborate a relation that already exists in the RelationsResponse.

3) Reference documentation about the relation (injected below).

# Reference documentation injected from `.adoc`

<relation_docs>
{relation_docs}
</relation_docs>

## Authoring requirements:
- Preserve the RelationsResponse semantics: for each relation record, map `subjectAttribute` on `subject` to `object`.
- Prefer concise, deterministic code. Add short inline comments only when they clarify decisions or cite evidence.

Output policy:
- Always return the full, final Groovy `relation` block for the current iteration (do not return diffs).
- If a chunk adds no useful information, keep the previous best result unchanged.
- No prose before or after the code. Only the Groovy block.
</instruction>


Output rules:
- Return ONLY a valid Groovy `relation` block based on documentation. No extra commentary.
- The example is illustrative; adapt to the format defined in the reference documentation.
- Do not introduce classes/attributes absent from the provided RelationsResponse.
""")


get_relation_user_prompt = textwrap.dedent("""\
Here is already extracted some object class and relation:

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
