# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt, build_operation_user_prompt
from src.modules.codegen.prompts.scim.shared_context_prompts import (
    SCIM_CONTRACT_CONTEXT_SYSTEM_RULES,
    SCIM_CONTRACT_CONTEXT_USER_SECTION,
    SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
)

_COMMON = build_operation_system_prompt(
    "search",
    context_rules=SCIM_CONTRACT_CONTEXT_SYSTEM_RULES + SCIM_NATIVE_OPERATION_DSL_SYSTEM_RULES,
    rules=r"""
- The target object class is "{object_class}". In Groovy, keep `objectClass("{object_class}")`
  exactly; in declarative YAML, keep the `objectClasses.{object_class}` key exactly.
- Built-in SCIM search provides UID retrieval, list-all and translatable filters. When the
  requested behavior needs no customization, emit the smallest complete document that still names
  the object class and the search operation - `objectClasses: {{ {object_class}: {{ search: {{}} }} }}`
  - never a bare `{{}}`, which names no object class at all. A search intent alone is not a server
  limitation.
- Respect the supplied ServiceProviderConfig and provider evidence. Do not infer server filtering
  support from response attributes, or declare a restriction merely because filter.supported is true.
- When actual server limitations must narrow accepted filters, use the documented Groovy
  search {{ scim {{ limitations {{ ... }} }} }} block. It has no YAML equivalent: convert the
  complete artifact to Groovy; never embed a standalone scim block in a YAML artifact.
- Declaring filter specifications changes acceptance behavior. Preserve list-all explicitly where
  needed by documented requirements; do not add restrictions just to implement the selected intent.
- The framework owns ordinary routing, SCIM filter translation, extraction and pagination.
  For a required deviation, use the documented custom search implementation.
- Before treating built-in list-all, or a custom search's `emptyFilterSupported true`, as
  complete, confirm from the target evidence that the SCIM resource endpoint itself returns the
  full object-class population rather than a caller-scoped or parent-scoped subset; a narrower
  resource needs the composition pattern below instead of a list-all declaration.
- There is no raw HTTP client or request builder documented for the custom search implementation
  block. The only way to reach another endpoint or object class from inside it is composing an
  already-implemented object class - the same `objectClass(name).search()`/`search(filter)`/
  `search(filter, resultHandler)`, `filter()`, `resultHandler()`, `operationOptions()`,
  `definition()`, and `attributeFilter(protocolName)` API documented for REST custom search. Never
  invent a different API inside that hook.
- Never generate sortingSupport or reference sorting.*; the reference does not document these hooks.
""",
)

get_scim_search_all_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: all
- Generate only necessary customization for list-all. Standard listing needs no operation script.
"""
)

get_scim_search_filter_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: filter
- Generate only documented filtering customization. An explicit filter.supported: false forbids generating server-side SCIM filtering.
"""
)

get_scim_search_id_system_prompt = (
    _COMMON
    + r"""
INTENT PROFILE: id
- Standard retrieval by __UID__ is built in. Do not add id filters, item endpoints or singleResult solely to reproduce it.
"""
)

get_scim_search_user_prompt = build_operation_user_prompt(SCIM_CONTRACT_CONTEXT_USER_SECTION, intent=True)
