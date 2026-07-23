# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_update_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (ConnId and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare a native SCIM `update` schema in Groovy for {object_class}.

The input data you will receive:
1. Attributes extracted from the SCIM schema for {object_class}.
2. A chunk of provider documentation containing target-specific capabilities and constraints.
3. Groovy output from previous chunks that you may minimally complete or edit.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Use the following generic SCIM `.adoc` documentation as the authority for update DSL syntax:

<update_docs>
{update_docs}
</update_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

UPDATE RULES:
- Generate native `update {{ scim {{ put {{ ... }} patch {{ ... }} }} }}` customization exactly as described by
  <update_docs>. Never represent PUT or PATCH as REST `endpoint(...)` blocks.
- Generate `patch {{ ... }}` only when `patch.supported` is true in <scim_service_provider_config>. An explicit false
  forbids PATCH. When the capability is missing, use PATCH only if <chunk> explicitly proves support.
- PUT is standard SCIM full replacement and is not disabled merely because ServiceProviderConfig has no PUT flag.
- Put `supportedAttributes`, `supportedAttribute(...)`, and attribute `limitations {{ ... }}` inside the appropriate
  native `put` or `patch` block.
- Exclude readOnly and immutable attributes from updates. Use the standalone SCIM schema as the authority for
  mutability, and use provider documentation only to narrow documented behavior.
- The target object class is "{object_class}". Keep `objectClass("{object_class}")` exactly.
- Never generate `endpoint(...)` anywhere in SCIM output. Resource endpoints and URLs in the supplied context are
  framework-owned metadata and must not be rendered into Groovy.
- Return ONLY valid Groovy code, with no explanation outside the code.
""")
)

get_scim_update_user_prompt = (
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
