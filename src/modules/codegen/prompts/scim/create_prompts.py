# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_create_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (ConnId and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare a native SCIM `create` schema in Groovy for {object_class}.

The input data you will receive:
1. Attributes extracted from the SCIM schema for {object_class}.
2. A chunk of provider documentation containing target-specific capabilities and constraints.
3. Groovy output from previous chunks that you may minimally complete or edit.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Use the following generic SCIM `.adoc` documentation as the authority for create DSL syntax:

<create_docs>
{create_docs}
</create_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

CREATE RULES:
- Generate native `create {{ scim {{ ... }} }}` customization exactly as described by <create_docs>.
- For a well-behaved SCIM server that needs no customization, preserve a minimal `create {{ }}` block; do not invent
  a POST endpoint, HTTP operation, request body, or response extractor.
- Put `supportedAttributes` and `supportedAttribute(...)` inside the native `scim {{ ... }}` block.
- Use <extracted_attributes> and the SCIM schema mutability/required metadata to select attributes. Provider examples
  may narrow support but must not introduce attributes absent from the supplied SCIM context.
- The target object class is "{object_class}". Keep `objectClass("{object_class}")` exactly.
- Never generate `endpoint(...)` anywhere in SCIM output. Resource endpoints and URLs in the supplied context are
  framework-owned metadata and must not be rendered into Groovy.
- Return ONLY valid Groovy code, with no explanation outside the code.
""")
)

get_scim_create_user_prompt = (
    textwrap.dedent("""\
Chunk {idx}/{total} of the SCIM schema:
Target object class: {object_class}

Extracted object-class attributes from the SCIM schema:

<extracted_attributes>
{attributes_json}
</extracted_attributes>
""")
    + SCIM_CONTRACT_CONTEXT_USER_SECTION
    + "{repair_user_suffix}"
    + textwrap.dedent("""\

Target-specific provider documentation for this iteration:
<chunk>
{chunk}
</chunk>

Result from previous chunks:
<result>
{result}
</result>
""")
)
