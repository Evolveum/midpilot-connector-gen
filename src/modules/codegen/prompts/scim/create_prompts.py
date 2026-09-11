# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.declarative_format_prompts import DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_create_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (ConnId and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare a `create` schema for {object_class}, in declarative YAML when the format is
sufficient or in Groovy otherwise.

The input data you will receive:
1. Attributes extracted from the SCIM schema for {object_class}.
2. A chunk of provider documentation containing target-specific capabilities and constraints.
3. Prior output from previous chunks that you may minimally complete or edit, in whichever format you chose.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Use the following generic SCIM `.adoc` documentation as the authority for create DSL syntax:

<create_docs>
{create_docs}
</create_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

CREATE RULES:
- IMPORTANT: <create_docs> and <declarative_docs> may describe a native SCIM `create {{ scim {{ ... }} }}`
  customization block (Groovy) or an equivalent declarative form. Check <create_docs> for a preview/
  in-development/not-yet-functional caveat on that block before generating it - when such a caveat is present,
  that customization does not work today. In that case, use the REST-style endpoint customization instead: a
  Groovy `create {{ endpoint(POST, "...") {{ ... }} }}` block, or its declarative `create.endpoints[]` YAML
  equivalent from <declarative_docs>.
- For a well-behaved SCIM server that needs no customization, and native create is confirmed functional, preserve
  a minimal `create {{ }}` block; do not invent a POST endpoint, HTTP operation, request body, or response
  extractor. If native create is not confirmed functional, use the smallest working REST-style
  `create.endpoints[]`/`endpoint(POST, ...)` equivalent instead of an empty block.
- Put attribute-narrowing (`supportedAttributes`/`supportedAttribute(...)` in Groovy, the endpoint's
  `supportedAttributes` in declarative YAML) inside the block that actually applies - the native `scim {{ ... }}`
  block when functional, otherwise the REST-style endpoint.
- Use <extracted_attributes> and the SCIM schema mutability/required metadata to select attributes. Provider examples
  may narrow support but must not introduce attributes absent from the supplied SCIM context.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in
  declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- Never generate a REST `endpoint(...)` wrapping native SCIM `create`, or vice versa - resource endpoints and
  URLs in the supplied context are framework-owned metadata for native SCIM and must not be rendered into it.
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
