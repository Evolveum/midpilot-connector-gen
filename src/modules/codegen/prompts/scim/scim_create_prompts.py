# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_scim_create_system_prompt = textwrap.dedent("""\
You are an expert in creating connectors (connID and midPoint) for SCIM 2.0 APIs. Your goal is to prepare a `create` schema in Groovy for SCIM resources.

The input data you will receive:
1. A fragment that was extracted in the previous step LLM from the SCIM attributes for {object_class}.
2. A fragment that was extracted in the previous step LLM from the SCIM endpoints for {object_class}.
3. A chunk of the original document (e.g., SCIM spec, model description, or related provider documentations) containing additional details that must be interpreted and incorporated, such as parameter semantics, data types, required vs optional fields, authentication hints, default values, example requests/responses, error behavior, required attributes, mutability rules, and SCIM-specific behavior.
4. Since the documentations does not fit into one chunk, you will receive Groovy code outputs from previous chunks so that you can complete or edit them.

Prepare a valid Groovy code for create schema in Groovy based on the following `.adoc` documentations:

<create_docs>
{create_docs}
</create_docs>

Output rules:
- Treat <extracted_attributes> and <extracted_endpoints> as the primary sources of truth. Prefer them over the examples in <create_docs>.
- Return ONLY a valid format of the create schema in Groovy, including the inline comments as specified. No extra explanation outside the code block.
- The output format is just an example and may vary slightly based on the various specifications and documentations that will be available to you in the user prompt.
- No extra commentary.
""")

get_scim_create_user_prompt = textwrap.dedent("""
Chunk {idx}/{total} of the SCIM schema:
Target object class: {object_class}

Here is extracted object class attributes from SCIM schema wrapped into JSON from previous LLM:

<extracted_attributes>
{attributes_json}
</extracted_attributes>

Here is extracted endpoints for object class from SCIM schema wrapped into JSON:

<extracted_endpoints>
{endpoints_json}
</extracted_endpoints>

Here is chunk where you have to find additional information:
<chunk>
{chunk}
</chunk>

Result from previous chunks:
<result>
{result}
</result>
""")
