# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

get_scim_update_system_prompt = build_operation_system_prompt(
    "update",
    context_rules=SCIM_CONTRACT_CONTEXT_SYSTEM_RULES + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
- Native SCIM update is functional: PATCH is the default and needs no script for a compliant server
  whose capabilities permit PATCH. An explicit patch.supported: false requires the documented PUT
  strategy when supported. If PATCH capability is unknown, use provider evidence and preserve a TODO
  for unresolved support rather than assume it.
- Use update {{ scim {{ put {{ ... }} }} }} to select full replacement; put and patch are mutually
  exclusive. PUT reads original state; default PATCH applies deltas without that read.
- Use supportedAttributes and per-attribute limitations inside the native put/patch strategy.
  Generate only documented operations and maxPerRequest restrictions.
- Native put/patch customization has no documented YAML equivalent. Use a complete Groovy artifact
  for it. Use minimal YAML only when defaults or generic enablement fully cover the requirement.
- Exclude readOnly and immutable attributes using the SCIM schema. Never wrap native strategies
  in REST endpoints or invent a resource path; the native framework owns standard routing.
""",
)

get_scim_update_user_prompt = build_operation_user_prompt(SCIM_CONTRACT_CONTEXT_USER_SECTION)
