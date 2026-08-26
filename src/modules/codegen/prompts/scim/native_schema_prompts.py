# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.native_schema_prompts import (
    build_native_schema_system_prompt,
    build_native_schema_user_prompt,
)
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
)

_SCIM_NATIVE_SCHEMA_SYSTEM_RULES = (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES
    + """
- The second argument of `connIdAttribute` and the first argument of `attribute(...)` MUST exactly match the
  native connector attribute name from `name` in <extracted_info>. When `name` and `scimAttribute` differ, use
  `name`; `scimAttribute` is the SCIM wire path only and belongs inside `scim {{ path ... }}`.
- When <connid_object_class> is not empty, use it only as attribute-exposure context. Generate ConnID mapping
  statements exclusively from `name` in <extracted_info>, never from the names in that projection.
"""
)

get_scim_native_schema_system_prompt = build_native_schema_system_prompt(_SCIM_NATIVE_SCHEMA_SYSTEM_RULES)
get_scim_native_schema_user_prompt = build_native_schema_user_prompt(SCIM_CONTRACT_CONTEXT_USER_SECTION)
