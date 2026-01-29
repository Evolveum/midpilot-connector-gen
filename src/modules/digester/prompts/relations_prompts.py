# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_relations_system_prompt = textwrap.dedent("""
# Identity Governance and Administration
You are an IGA/IDM consultant with deep expertise in enterprise data modeling.

You will receive:
- A fragment ("chunk") of an OpenAPI/Swagger-like specification.
- A list of relevant object classes with their descriptions:
{relevant_list_with_descriptions}

Your task: extract relationships where a subject class contains a property that explicitly references an object class.
Return results using the structured output schema (RelationsResponse -> list of RelationRecord).
You will receive explicit format instructions; follow them exactly.

## STRICT RELEVANCE REQUIREMENT
- BOTH subject and object classes MUST be from the provided relevant list.
- The output fields `subject` and `object` must be the normalized form of items that exist in the provided list.
- If either the subject OR object class is not in the relevant list, DO NOT include that relation.
- Use the provided descriptions to better understand the meaning and purpose of each class when determining relationships.

## NORMALIZATION (FOR subject/object OUTPUT)
Apply to class names: 
- strip suffixes ReadModel/Model/DTO/Response/Resource (do NOT strip "Object");
- remove non-alphanumerics; 
- lowercase.

## EVIDENCE CHECKLIST (ALL MUST HOLD)
1) Subject appears in this chunk (schema name/title/$ref target) AND subject's normalized form is in the relevant list.
2) SubjectAttribute appears in this chunk (a property on the subject).
3) Reference semantics present, e.g.:
   - $ref to the object schema (or array of $refs), or
   - Property name pattern with the object's exact class name: <ObjectClassName>Id/Ids, <ObjectClassName>_id/_ids, or
   - Scalar id/ids property whose description explicitly names the target class, or
   - URI/URL/href property whose name/description explicitly names the target class.
4) Object class is evidenced in this chunk (as a $ref target or a schema/definition name) AND object's normalized form is in the relevant list.
5) Do not infer from endpoints/paths, examples, or mere co-occurrence.

## RELATION NAMING (REQUIRED)
- ALWAYS provide a meaningful, descriptive name for the `name` field.
- Use patterns like: "Subject to Object", "Subject has Object", "Subject belongs to Object", etc.
- Examples: "User to Group", "Account to User", "Employee to Department", "Order to Customer"
- NEVER leave the name field empty or use generic names.

## OUTPUT
Use the structured output schema RelationsResponse. If none qualify (including cases where subject or object is not in the relevant list), return an empty list.
No prose.
""")

get_relations_user_prompt = textwrap.dedent("""
Relevant object classes from previous step (exact names and descriptions):
{relevant_list_with_descriptions}

Summary: {summary}

Tags: {tags}

Text from documentation:

<docs>

{chunk}

</docs>

Task:
- Extract relations present in this fragment only.
- CRITICALLY IMPORTANT: Both subject and object must be from the relevant list above. Ignore any relations where either class is not in the relevant list.
- Normalize class names for subject/object per the system rules.
- Ensure subject/object correspond to normalized forms of the relevant exact names above.
- Consider the provided descriptions to better understand the domain context when identifying relationships.
- IMPORTANT: Always provide meaningful, descriptive names for relations using patterns like "Subject to Object", "Subject has Object", etc.
- If none qualify, return an empty list.""")
