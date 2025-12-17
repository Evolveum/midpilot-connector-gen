# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_object_class_schema_system_prompt = textwrap.dedent("""

<instruction>
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a fragment, focused excerpt of the application’s OpenAPI schema containing definition and description
  (its `properties`, and possibly `required`, `readOnly`/`writeOnly`, `deprecated`, or $refs).

Your task: extract ONLY attributes that are explicitly defined under that object’s `properties`.
Use the structured output schema (ObjectClassSchemaResponse -> AttributeInfo) to respond.
Do NOT infer or invent attributes. If the object is not present or has no `properties`, return an empty map.

Rules
- Include a property **only if it appears under this object's `properties`**.
- type:
  - Use the JSON Schema type if present.
  - If `$ref: '#/components/schemas/Other'`, set: "type": "reference Other", "format": "reference".
  - If inline object (has nested `properties`) → "type": "object", "format": "embedded".
- format:
  - For primitives, use OpenAPI format registry values if present (e.g., "email", "uri", "int64", "date-time"); otherwise "".
  - For arrays, set format to the **item** format ("" if none).
  - For object/reference, "embedded" or "reference" as above (no custom values).
- description: use the property’s description if present, else "".
- mandatory: true if the property name is in this object’s `required` array.
- updatable: false if "readOnly": true; otherwise true.
- creatable: false if "readOnly": true; otherwise true. (Do not guess from endpoints.)
- readable: false if "writeOnly": true; otherwise true.
- multivalue: true if property "type" == "array"; otherwise false.
- returnedByDefault: boolean, Is attribute returned by default? Eg. attributes which requires fetching additional endpoint to resolve should.

Hard constraints
- Do NOT add attributes from examples, other objects, or unrelated sections.
- Do NOT return keys that are not in `properties`.
- If unsure, omit the attribute or return an empty map.
</instruction>
""")

get_object_class_schema_user_prompt = textwrap.dedent("""
Object Class: {object_class}

Summary: {summary}

Tags: {tags}

Text from documentation:

<docs>

{chunk}

</docs>

Extract attributes for {object_class} from this chunk using the structured output schema.
Follow the Rules from the system prompt. If none are present, return an empty map.
""")


get_filter_duplicates_system_prompt = textwrap.dedent("""
<instruction>
You will receive:
- the target object class name (e.g., "User")
- attribute *candidates* grouped by attribute name
  (for each attribute name there is at least one candidate)
- each candidate has:
  - info: the parsed AttributeInfo (type, format, description, flags)

Task:
For EACH ATTRIBUTE NAME that appears in the candidates, return EXACTLY ONE candidate
(the best one). Your main job is to DISAMBIGUATE between multiple variants of the same
attribute name, not to remove attributes.

Use the evidence to choose the best variant:
- prefer the candidate whose description or path looks closer to the target object class
- prefer the candidate that is more complete (non-empty description, useful flags)
- if all candidates are generic REST / HAL / HATEOAS-style fields (e.g. "_links", "_embedded")
  you MUST still return one of them

Only OMIT an attribute name if ALL its candidates are clearly about a different domain
than the target object class.

Rules:
- Do NOT merge or invent fields; choose ONE of the provided candidates for each name.
- Do NOT drop attributes just because the evidence does not repeat the object class name.
- Return the result using ObjectClassSchemaResponse, where `attributes` is a map of
  <propertyName> -> AttributeInfo. If truly no attributes qualify, return an empty map.
</instruction>
""")

get_filter_duplicates_user_prompt = textwrap.dedent("""
Object Class: {object_class}

<candidates>

{candidates_json}

</candidates>
""")
