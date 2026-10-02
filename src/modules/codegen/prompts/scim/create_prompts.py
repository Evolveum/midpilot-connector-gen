# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_create_system_prompt = build_operation_system_prompt(
    "create",
    context_rules=SCIM_CONTRACT_CONTEXT_SYSTEM_RULES + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
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
  URLs are framework-owned for native operations; use the resource endpoint for the documented REST create fallback.
""",
)

get_scim_create_user_prompt = build_operation_user_prompt(SCIM_CONTRACT_CONTEXT_USER_SECTION)
