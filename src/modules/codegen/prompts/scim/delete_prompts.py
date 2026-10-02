# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_delete_system_prompt = build_operation_system_prompt(
    "delete",
    context_rules=SCIM_CONTRACT_CONTEXT_SYSTEM_RULES + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
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
""",
)

get_scim_delete_user_prompt = build_operation_user_prompt(SCIM_CONTRACT_CONTEXT_USER_SECTION)
