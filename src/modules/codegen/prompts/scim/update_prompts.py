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

get_scim_update_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (ConnId and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare an `update` schema for {object_class}, in declarative YAML when the format
is sufficient or in Groovy otherwise.

The input data you will receive:
1. Attributes extracted from the SCIM schema for {object_class}.
2. A chunk of provider documentation containing target-specific capabilities and constraints.
3. Prior output from previous chunks that you may minimally complete or edit, in whichever format you chose.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Use the following generic SCIM `.adoc` documentation as the authority for update DSL syntax:

<update_docs>
{update_docs}
</update_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

UPDATE RULES:
- IMPORTANT: <update_docs> and <declarative_docs> may describe a native SCIM `update {{ scim {{ put {{ ... }}
  patch {{ ... }} }} }}` customization block (Groovy) or an equivalent declarative form. Check <update_docs> for
  a preview/in-development/not-yet-functional caveat on that block before generating it - when such a caveat is
  present, that customization does not work today. In that case, use the REST-style endpoint customization
  instead: a Groovy `update {{ endpoint(PUT/PATCH, "...") {{ ... }} }}` block, or its declarative
  `update.endpoints[]` YAML equivalent from <declarative_docs>. Never represent PUT/PATCH as a REST
  `endpoint(...)` when native SCIM `put`/`patch` is confirmed functional by the documentation, and never do the
  reverse.
- Generate `patch {{ ... }}` (native SCIM) only when it is confirmed functional above AND `patch.supported` is
  true in <scim_service_provider_config>. An explicit false forbids PATCH. When the capability is missing, use
  PATCH only if <chunk> explicitly proves support.
- PUT is standard SCIM full replacement and is not disabled merely because ServiceProviderConfig has no PUT flag.
- Put attribute-narrowing (`supportedAttributes`, `supportedAttribute(...)`, attribute `limitations {{ ... }}` in
  Groovy; the endpoint's `supportedAttributes` in declarative YAML) inside the block that actually applies -
  the native `put`/`patch` block when functional, otherwise the REST-style endpoint.
- Exclude readOnly and immutable attributes from updates. Use the standalone SCIM schema as the authority for
  mutability, and use provider documentation only to narrow documented behavior.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in
  declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- Never generate a REST `endpoint(...)` wrapping native SCIM `put`/`patch`, or vice versa - resource endpoints
  and URLs in the supplied context are framework-owned metadata for native SCIM and must not be rendered into it.
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
