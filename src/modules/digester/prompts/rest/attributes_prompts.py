# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_object_class_schema_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a fragment, focused excerpt of the application’s OpenAPI schema containing definition and description
  (its `properties`, and possibly `required`, `readOnly`/`writeOnly`, `deprecated`, or $refs).

Your task: extract ONLY attributes that are explicitly defined under that object’s `properties`.
Use the structured output schema (ObjectClassSchemaResponse -> AttributeInfo) to respond.
Do NOT infer or invent attributes. If the object is not present or has no `properties`, return an empty map.

Rules:
- Include a property **only if it appears under this object's `properties`**.
- type:
  - Use the JSON Schema type if present.
  - If `$ref: '#/components/schemas/Other'`, set: "type": "reference Other", "format": "reference".
  - If inline object (has nested `properties`) → "type": "object", "format": "embedded".
  - If not explicitly stated in this chunk, set type to null.
- format:
  - For primitives, use OpenAPI format registry values if present (e.g., "email", "uri", "int64", "date-time"); otherwise null.
  - For arrays, set format to the **item** format (null if none).
  - For object/reference, "embedded" or "reference" as above (no custom values).
  - If not explicitly stated in this chunk, set format to null.
- description: use the property’s description if present, else null.
- mandatory: true if the property name is in this object’s `required` array; null if `required` is not present.
- updatable: false if "readOnly": true; otherwise true. If readOnly is not present, use null.
- creatable: false if "readOnly": true; otherwise true. If readOnly is not present, use null. (Do not guess from endpoints.)
- readable: false if "writeOnly": true; otherwise true. If writeOnly is not present, use null.
- multivalue: true if property "type" == "array"; otherwise false. If type is not present, use null.
- returnedByDefault: true if explicitly stated that attribute is returned by default; false if explicitly stated otherwise; null if unknown.

Hard constrains:
- Do NOT add attributes from examples, other objects, or unrelated sections.
- Do NOT return keys that are not in `properties`.
- If unsure, omit the attribute or return an empty map.
- Ignore ANY keys that appear under `example:`, `examples:`, or `value:` blocks. NEVER extract from examples.
</instruction>
""")

get_object_class_schema_user_prompt = textwrap.dedent("""
Object Class: {object_class}

Summary of the chunk:
 
<summary>
{summary}
</summary>

Tags of the chunk:
 
<tags>
{tags}
</tags>

Text from documentation:

<chunk>
{chunk}
</chunk>

Extract attributes for {object_class} from this chunk using the structured output schema.
Follow the Rules from the system prompt. If none are present, return an empty map.
""")


get_filter_duplicates_system_prompt = textwrap.dedent("""
<instruction>
You will receive:
- the target object class name
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
- prefer the candidate that is more complete (non-null description, useful flags)
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


get_fill_missing_details_system_prompt = textwrap.dedent("""
<instruction>
You will receive:
- the target object class name
- current merged attributes (`attributes_json`) where some fields may be null/empty
- documentation excerpts (`docs_payload`) from relevant chunks

Task:
Return the SAME attribute map shape using ObjectClassSchemaResponse -> AttributeInfo.
Fill only missing values when they are explicitly supported by the provided documentation.

Rules:
- Keep all existing non-null/non-empty values unchanged.
- Do NOT add new attribute names.
- Do NOT remove existing attribute names.
- For each attribute field currently null/empty, fill it only if documentation clearly supports it.
- If evidence is missing/unclear, keep that field null.
- Use these conventions when filling:
  - mandatory: true if in object's `required`; false if explicitly not required; null if unknown.
  - updatable/creatable: false if readOnly=true; true only when explicitly supported by schema rules; null if unknown.
  - readable: false if writeOnly=true; true only when explicitly supported; null if unknown.
  - multivalue: true for array type; false for non-array type; null if unknown.
  - type/format/description/returnedByDefault: fill only from explicit evidence.

Hard constraints:
- Do NOT invent data.
- Do NOT use knowledge outside the provided docs payload.
- If nothing can be improved, return the attributes exactly as received.
</instruction>
""")


get_fill_missing_details_user_prompt = textwrap.dedent("""
Object Class: {object_class}

<attributes_json>
{attributes_json}
</attributes_json>

<chunk>
{docs_payload}
</chunk>

Fill only missing fields according to the rules and return ObjectClassSchemaResponse.
""")


get_attribute_discovery_system_prompt = textwrap.dedent("""
<instruction>
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a fragment, focused excerpt of the application’s OpenAPI schema or API documentation containing definition and description
  (its `properties`, and possibly `required`, `readOnly`/`writeOnly`, `deprecated`, or $refs).

Your task: extract ONLY attributes that are explicitly defined as that object's `properties` in the documentation.
Use the structured output schema to respond.
Do NOT infer or invent attributes. If the object is not present or has no `properties`, return an empty map.

Rules:
- The attribute should be clearly described as a property of the given object class.
- type:
  - Use the JSON Schema type if present.
  - If `$ref: '#/components/schemas/NAME'`, set: "type": "reference NAME", "format": "reference".
  - If inline object (has nested `properties`) → "type": "object", "format": "embedded".
  - If not explicitly stated in this chunk, set type to null.
- format:
  - For primitives, use OpenAPI format registry values if present (e.g., "email", "uri", "int64", "date-time"); otherwise null.
  - For arrays, set format to the **item** format (null if none).
  - For object/reference, "embedded" or "reference" as above (no custom values).
  - If not explicitly stated in this chunk, set format to null.
- description: use the property’s description if present, else null.

Hard constrains:
- Do NOT add attributes from examples, other objects, or unrelated sections.
- If unsure, omit the attribute or return an empty map.
- Ignore ANY keys that appear under `example:`, `examples:`, or `value:` blocks. NEVER extract from examples.
- Ignore all attribute name matching `customField` with number 
- Ignore all attribute name matching `mail` - BUT `e-mail` is correct
- Ignore all attribute name matching `identityUrl` - BUT `identity_url` is correct
- Ignore all deprecated, in progress, not yet implemented (whole attributes or any other relevant part) or other non release-ready attributes
                                                        
**OUTPUT REQUIREMENTS**

For each attribute you extract, provide:

1. **name** - The attribute name as presented in the documentation
2. **type** - The attribute type as defined above.
3. **format** - The attribute format as defined above.
4. **description** - The attribute description as defined above.
5. **sequences** - An array of objects, each containing:
   - **start_sequence** - The exact opening phrase, start of the sequence - marker from the documentation (word-for-word, searchable)
   - **end_sequence** - The exact closing phrase, end of the sequence - marker from the documentation (word-for-word, searchable)
                                
**Sequences**
- Sequences are fragments of text that define the attribute in the documentation.
- Sequences are used for extracting details about the attribute so they should encompass all of the relevant information about the attribute and nothing more.
- Sequences is an array of objects - you should return all relevant sequences, each object is defined as text between two unique markers - start_marker and end_marker. Text of the marker is also used as a part of the sequence.
- Sequences for an attribute must be unique - while multiple sequences per attribute are encuraged, they should not be an exact copy of each other.
                                
***MARKER EXTRACTION RULES**

- **Accuracy**: Copy markers exactly as they appear—word-for-word, character-for-character, including punctuation, whitespace, and line breaks. No paraphrasing or abbreviation.
- **Content span**: Markers should encompass the entire relevant section, including examples, edge cases, constraints, and related context.
- **Uniqueness**: Markers must be distinctive strings that can reliably locate the exact position in the documentation. Avoid common words or patterns.
  - Uniqueness of the start marker is crucial for accurate extraction, but some leniency can be applied to end markers.
- **Length constraints**:
  - Minimum: 10 characters (shorter markers will be discarded as insufficiently unique)
  - Maximum: 300 characters (longer markers reduce searchability)
- **Positioning**:
  - Ideal start marker: The title or opening sentence introducing the attribute
  - Ideal end marker: The final sentence concluding the attribute's description
- **JSON/YAML documentation**: Prioritize uniqueness of markers; include specific text that distinguishes this attribute from others.
- **Conciseness**: Make markers as short as possible while maintaining uniqueness and clear attribution to the attribute.
- **Multiple locations**: If an attribute is discussed in multiple documentation sections, return separate start/end marker pairs for each section—do not span unrelated content.
- **Forbidden practices**:
  - Never include the content between markers—return only the markers themselves
- **Special characters**: Do not forget punctuation, parentheses, colons, newlines, and other non-word characters present in the markers.
- **Relevance**: Markers should be extracted only and only from sections that are clearly tied to the given object class. Never include markers from sections that are about a different object class, even if the attribute name is the same.
</instruction>""")

get_attribute_discovery_user_prompt = textwrap.dedent("""
Object Class: {object_class}

Summary of the chunk:
 
<summary>
{summary}
</summary>

Tags of the chunk:
 
<tags>
{tags}
</tags>

Text from documentation:

<chunk>
{chunk}
</chunk>

Extract attributes for {object_class} from this chunk using the structured output schema.
Follow the Rules from the system prompt. If none are present, return an empty map.""")

attribute_deduplication_system_prompt = textwrap.dedent("""
You are an expert documentation analyst specializing in identifying and deduplicating API attributes.

You will receive a list of attribute entries for one object class. Each entry may include:
- name
- type/format/description
- flags (mandatory/updatable/creatable/readable/multivalue/returnedByDefault)
- relevant sequences with source evidence

Your task is to identify likely duplicates and weak/irrelevant attributes.

Rules for `duplicates`:
- Return pairs in this shape: [keep_name, delete_name].
- Use attribute names exactly as they appear in the provided list.
- `keep_name` must be the better candidate (more complete and better supported by evidence).
- Prefer deduplication over deletion when two entries represent the same conceptual attribute.
- Do not invent names that are not present in the input.
- In case of two candidates with different casing (e.g. snake_case vs camelCase) prefer the one that matches the casing style of the majority of attributes in the list, or the one that is more common in the documentation.

Rules for `to_be_deleted`:
- Include names that should be removed because evidence is weak, irrelevant to the object class, or clearly noise.
- Do not include names already listed as `delete_name` in duplicates unless absolutely necessary.
- Include all attributes that are deprecated, marked as "in progress" or "not yet implemented", or otherwise clearly not release-ready.
- Include non-relevant attributes for IDM integration like "self".
- Use names exactly as present in the input.

Quality guidance:
- Prefer entries with stronger, clearer descriptions and richer non-null metadata.
- Prefer entries backed by relevant sequences.
- Be conservative: if uncertain, keep the attribute.
""")


attribute_deduplication_user_prompt = textwrap.dedent("""
Object Class: {object_class}

List of attribute candidates:
{attributes_list}

Please return:
1. `duplicates`: list of [keep_name, delete_name] pairs.
2. `to_be_deleted`: list of attribute names to remove.
""")

get_build_from_sequences_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- Attribute json object with these fields:
  - name
  - type
  - format
  - description
  - flags (mandatory/updatable/creatable/readable/multivalue, etc.)
  - relevant sequences with source evidence

Your primary task is to fill in any missing or null fields in the AttributeInfo using ONLY the provided relevant sequences as evidence. Each sequence includes a start and end marker that corresponds to a specific section of the documentation.
Only fill those where there is clear, explicit evidence in the relevant sequences. Do NOT infer or guess values that are not directly supported by the text in those sequences.
                                                         
Your secondary task is to verify the existing non-null fields and correct them if there is clear and irrefutable evidence in the relevant sequences that they are wrong.
However, with this second task be very conservative in making corrections. Only change existing non-null values if the evidence is overwhelmingly clear and unambiguous.
                                                         
Rules:
- For each field currently null or missing, fill it only if there is explicit, unambiguous evidence in the relevant sequences.
- For each field currently non-null, only change it if the relevant sequences provide overwhelmingly clear and irrefutable evidence that it is incorrect.
- Be careful to not set a field if the evidence is missing, unclear, or contradictory. In such cases, keep the field null or unchanged.
- Implied evidence is not sufficient; there must be direct, explicit statements in the relevant sequences to support any changes or fillings.               
- Be careful with time related attributes (e.g., "created", "modified" times) and their mandatory flag - there can be differences between "mandatory" flag in IDM integration sense and the application's point of view.
- Only set the mandatory flag to true if the documentation explicitly states that the attribute is required in IDM integration point of view.                                    
- When a field can be filled as a list, set the multivalue flag to true.
- type:
  - Use the JSON Schema type if present.
  - If `$ref: '#/components/schemas/NAME'`, set: "type": "reference NAME", "format": "reference".
  - If inline object (has nested `properties`) → "type": "object", "format": "embedded".
  - If not explicitly stated in this chunk, set type to null.
- format:
  - For primitives, use OpenAPI format registry values if present (e.g., "email", "uri", "int64", "date-time"); otherwise null.
  - For arrays, set format to the **item** format (null if none).
  - For object/reference, "embedded" or "reference" as above (no custom values).
  - If not explicitly stated in this chunk, set format to null.

Hard constraints:
  - Do NOT invent data.
  - Do NOT use knowledge outside the provided docs payload.
  - If nothing can be improved, return the attributes exactly as received.
""")

get_build_from_sequences_user_prompt = textwrap.dedent("""
Object Class: {object_class}

<attribute_json>
{attribute_json}
</attribute_json>

Fill primarily the missing fields, in case that there is clear evidence in the relevant sequences.
You can also correct existing non-null values if there is overwhelming evidence that they are wrong.

Return the json object based on format instructions.
""")

get_consolidate_attributes_system_prompt = textwrap.dedent("""
You are an expert IGA/IDM analyst specializing in consolidating and refining API attribute information.
You will be given:
- an object class name (e.g. "User", "Group", ...)
- an attribute json object with these fields:
  - name
  - type
  - format
  - description
  - flags (mandatory/updatable/creatable/readable/multivalue, etc.)
  - relevant sequences with source evidence

Your primary task is to review the provided attribute information and produce a consolidated and refined version of it, ensuring that all fields are as accurate as possible based on the provided evidence in the relevant sequences.                                                           

Rules:
- Be very conservative in making any changes to the existing non-null values. Only change them if the relevant sequences provide overwhelmingly clear and irrefutable evidence that they are incorrect.
- Do not add any flags that are currently null
- type:
  - Use the JSON Schema type if present.
  - If `$ref: '#/components/schemas/NAME'`, set: "type": "reference NAME", "format": "reference".
  - If inline object (has nested `properties`) → "type": "object", "format": "embedded".
  - If not explicitly stated in this chunk, set type to null.
- format:
  - For primitives, use OpenAPI format registry values if present (e.g., "email", "uri", "int64", "date-time"); otherwise null.
  - For arrays, set format to the **item** format (null if none).
  - For object/reference, "embedded" or "reference" as above (no custom values).
  - If not explicitly stated in this chunk, set format to null.

Hard constraints:
- Do NOT invent data.
- Do NOT use knowledge outside the provided docs payload.
- If nothing can be improved, return the attributes exactly as received.
""")

get_consolidate_attributes_user_prompt = textwrap.dedent("""
Object Class: {object_class}
                                                         
<attribute_json>
{attribute_json}
</attribute_json>
                                                         
Review the provided attribute information and produce a consolidated and refined version of it, ensuring that all fields are as accurate as possible based on the provided evidence in the relevant sequences.
Do not change the null flags/fields.
Return the json object based on format instructions.
""")

