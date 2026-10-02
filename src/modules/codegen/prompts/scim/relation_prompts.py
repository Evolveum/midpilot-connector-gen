# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

"""SCIM relation generation with logical attributes and wire bindings kept separate."""

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt
from src.modules.codegen.prompts.relation_prompts import RELATION_RULES, get_relation_user_prompt

get_scim_relation_system_prompt = build_operation_system_prompt(
    "relation",
    rules=RELATION_RULES
    + r"""

SCIM-SPECIFIC REQUIREMENTS:
- Preserve the logical application/ConnId attribute from the selected relation. Never replace
  it with the standard SCIM spelling merely because the standard name is familiar.
- Treat a differing `scimPath` as a wire binding. For example, when the record uses `Username`
  and evidence maps it to `userName`, the relationship attribute remains `Username`; SCIM
  access/filter/serialization uses `userName` only where the DSL actually needs the wire path.
- A complex reference has two distinct names: the selected relation uses its parent logical
  carrier (`groups`, `members`, or `manager`), while `scimEvidence.scimPath` can identify the
  nested wire reference (`groups.$ref`, `members.$ref`, or `manager.$ref`). Never replace the
  relation attribute with the bare `$ref`/`value` leaf. Apply multi-valued behavior to the
  parent carrier, not to the leaf sub-attribute.
- Vendor documentation overrides assumptions based on RFC-standard names. Ground custom
  casing, extensions, referenceTypes, and complex `value`/`$ref` layouts in supplied evidence.
- Do not automatically generate User/groups or Group/members merely because SCIM commonly has
  them. Generate only the selected relation named `{relation_name}`.
- `schemaExtensions` composition is not a relationship resolver by itself.
- Generate resolvers only between independently manageable ResourceTypes. A schema extension,
  complex type, schema/attribute definition, shadow representation, schema URI, or objectClass
  discriminator is not a resource instance and cannot be a relation end or link object.
- Core `roles`/`entitlements` values become relations to standalone Role/Entitlement resources
  only when the selected vendor evidence explicitly maps their value/id/$ref to that resource.
- When `kind` is `inverse_reference`, resolve through the documented object-side SCIM filter.
  When it is `link_object`, ground both ends in the documented carrying resource and its
  `linkAttributes`. Do not invent a direct attribute on either end.
- An empty relation attribute is unknown. Use only a resolver explicitly supported by the
  supplied documentation/evidence; never fill it with a conventional SCIM name.
- Represent both directions of one association in one `relationship` block. Keep distinct
  associations between the same classes separate.

""",
)

get_scim_relation_user_prompt = get_relation_user_prompt
