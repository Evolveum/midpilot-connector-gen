# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from src.modules.codegen.prompts.operation_prompts import build_operation_system_prompt

get_relation_system_prompt = build_operation_system_prompt(
    "relation",
    rules=r"""
- Target protocol: {protocol}. Generate only the selected relationship named {relation_name}.
- Preserve the selected relation's subject, object, subjectAttribute and objectAttribute. Do not
  invent participants or infer a different relationship from endpoint examples.
- One bidirectional association is one relationship containing both participants. Merge duplicate
  descriptions of the same association rather than create multiple blocks.
- For REST and SCIM, prefer the documented root-level relationships YAML map when sufficient.
  If an attribute is already resolved automatically (for example standard SCIM groups/members),
  preserve framework defaults rather than install a duplicate resolver.
- A required scripted resolver may use the documented YAML implementation hook. Otherwise use a
  complete Groovy relationship("...") block. Never emit a non-existent relation {{ ... }} DSL.
- For SQL, relationships are detected from foreign-key metadata and conventions. Scripted
  relationship overrides are unsupported. Do not fabricate a relationship block or a join API.
  If discovery suffices, emit {{}}. If the selected relationship needs an unsupported override,
  emit {{}} with a concise YAML TODO identifying the unmet requirement.
- The separate relationship artifact defines the association only. Required search implementations
  belong in the corresponding object-class search artifacts; identify any missing support with a TODO.
""",
)

get_relation_user_prompt = """
Requested relationship:
<relation_name>
{relation_name}
</relation_name>
<extracted_relations>
{relation_json}
</extracted_relations>
Target-system documentation for this iteration:
<chunk>
{chunk}
</chunk>
Last accepted artifact:
<result>
{result}
</result>
"""
