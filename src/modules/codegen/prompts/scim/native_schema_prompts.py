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
- The native connector attribute name for every mapping - however your chosen format expresses it
  (`connIdAttribute(...)`/`attribute(...)` in Groovy, or the `connId`/`attributes` keys in declarative YAML) -
  MUST exactly match `name` in <extracted_info>. When `name` and `scimAttribute` differ, use `name`;
  `scimAttribute` is the SCIM wire path only and belongs inside `scim {{ path ... }}` (Groovy) or the
  attribute's `scim.path` key (declarative YAML).
- When <connid_object_class> is not empty, use it only as attribute-exposure context. Generate ConnID mapping
  statements exclusively from `name` in <extracted_info>, never from the names in that projection.
- A SCIM complex attribute (`openApiFormat: "embedded"`) whose SCIM name (`scimAttribute`, else `name`) is listed
  in <scim_flatten_candidates> is flattened, not declared: add that SCIM name to the object class `scim.flatten`
  list (Groovy: `scim {{ flatten "name" }}`) and do not declare it under the attributes; the framework derives
  the flat attributes. Never flatten a name missing from <scim_flatten_candidates>, because connector schema
  discovery rejects it. Map any other complex attribute, or a documented sub-attribute that flattening does not
  produce (e.g. `emails[primary eq true].value`), as a flat attribute with `scim.path`, or leave a TODO comment.
"""
)

_SCIM_NATIVE_SCHEMA_USER_SECTION = (
    SCIM_CONTRACT_CONTEXT_USER_SECTION
    + """
SCIM complex attributes that `scim.flatten` accepts (computed from <scim_protocol_schema>):

<scim_flatten_candidates>
{scim_flatten_candidates_json}
</scim_flatten_candidates>
"""
)

get_scim_native_schema_system_prompt = build_native_schema_system_prompt(_SCIM_NATIVE_SCHEMA_SYSTEM_RULES)
get_scim_native_schema_user_prompt = build_native_schema_user_prompt(_SCIM_NATIVE_SCHEMA_USER_SECTION)
