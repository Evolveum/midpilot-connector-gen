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

get_scim_delete_system_prompt = (
    textwrap.dedent("""\
You are an expert in creating connectors (ConnId and midPoint) for SCIM 2.0 APIs.
Your goal is to prepare a `delete` schema for {object_class}, in declarative YAML when the format is
sufficient or in Groovy otherwise.

The input data you will receive:
1. Attributes extracted from the SCIM schema for {object_class}.
2. A chunk of provider documentation containing target-specific capabilities and constraints.
3. Prior output from previous chunks that you may minimally complete or edit, in whichever format you chose.
4. Separate SCIM schema, resource, connector-object-class, and service-provider capability views.

Use the following generic SCIM `.adoc` documentation as the authority for delete DSL syntax:

<delete_docs>
{delete_docs}
</delete_docs>
""")
    + SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES
    + DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
    + "{repair_system_suffix}"
    + textwrap.dedent("""\

DELETE RULES:
- For standard SCIM delete, native delete needs no customization: omit the block entirely, or - if declarative
  YAML for this connector already exists for other operations - a minimal `delete: {{}}`/omitted `delete` key.
  In Groovy, a minimal `delete {{ }}` block is likewise optional; only emit it if <result> already establishes
  one. The SCIM framework appends the resource identifier and performs DELETE; do not generate a REST endpoint
  or request block for standard behavior.
- Never generate `endpoint(...)` anywhere in native SCIM output. Resource endpoints and URLs in the supplied
  context are framework-owned metadata and must not be rendered into it.
- Generate provider-specific delete customization only when <delete_docs> or <declarative_docs> permits it and
  <chunk> explicitly proves non-standard behavior such as soft delete. Express it using the native SCIM form
  documented there (Groovy or declarative YAML), never REST DSL, unless <delete_docs> itself points at a
  REST-style endpoint fallback for delete.
- Do not generate delete for an extension schema or embedded complex attribute without its own resource contract.
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")` exactly; in
  declarative YAML, keep the `objectClasses.{object_class}` key exactly.
""")
)

get_scim_delete_user_prompt = (
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
