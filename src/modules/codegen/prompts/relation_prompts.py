# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt

RELATION_RULES = r"""
- Target protocol: {protocol}. Generate only the selected relationship named {relation_name}.
- Preserve the selected relation's subject, object, subjectAttribute and objectAttribute. Do not
  invent participants or infer a different relationship from endpoint examples.
- Stored analysis describes how the selected association is carried: `reference` means the subject
  holds the pointer; `inverse_reference` means only the object holds it; `link_object` names a
  separate carrying class in `linkObjectClass`, with endpoint bindings in `linkAttributes`;
  `virtual_endpoint` means the link exists only as a documented API path. An empty analysis
  means no matching analysis was available: rely on the selected record and documentation.
- For `link_object`, ground both ends in the carrying class's attributes and documented search
  surface. Do not invent a direct attribute on either end. For `inverse_reference` and
  `virtual_endpoint`, use only a documented resolver on the side without a stored pointer.
- An empty subjectAttribute or objectAttribute is unknown, not permission to invent a name.
  Use the framework reference to determine whether the selected association is expressible.
- One bidirectional association is one relationship containing both participants. Merge duplicate
  descriptions of the same association rather than create multiple blocks.
- For REST and SCIM, declarative YAML cannot declare relationships: the connector rejects a
  root-level relationships YAML map. Use a complete Groovy relationship("...") block. If an
  attribute is already resolved automatically (for example standard SCIM groups/members),
  preserve framework defaults rather than install a duplicate resolver. Never emit a
  non-existent relation {{ ... }} DSL.
- For SQL, relationships are detected from foreign-key metadata and conventions. Scripted
  relationship overrides are unsupported. Do not fabricate a relationship block or a join API.
  If discovery suffices, emit {{}}. If the selected relationship needs an unsupported override,
  emit {{}} with a concise YAML TODO identifying the unmet requirement.
- The separate relationship artifact defines the association only. Required search implementations
  belong in the corresponding object-class search artifacts; identify any missing support with a TODO.
"""

get_relation_system_prompt = build_operation_system_prompt("relation", rules=RELATION_RULES)

get_relation_user_prompt = """
Requested relationship:
<relation_name>
{relation_name}
</relation_name>
<extracted_relations>
{relation_json}
</extracted_relations>
Stored analysis of the selected relation:
<relation_analysis>
{relation_context_json}
</relation_analysis>
Target-system documentation for this iteration:
<chunk>
{chunk}
</chunk>
Last accepted artifact:
<result>
{result}
</result>
"""
