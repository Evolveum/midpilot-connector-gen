# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

get_attribute_discovery_system_prompt = textwrap.dedent("""
<instruction>
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a fragment, focused excerpt of the application’s OpenAPI schema or API documentation containing definition and description
  (its `properties`, and possibly `required`, `readOnly`/`writeOnly`, `deprecated`, or $refs).

Your task: discover ONLY attribute names and descriptions that are explicitly defined as that object's `properties` in the documentation.
Use the structured output schema to respond.
Do NOT infer or invent attributes. If the object is not present or has no `properties`, return an empty map.

Rules:
- The attribute should be clearly described as a property of the given object class.
- description: use the property’s description if present, else null.
- Do not extract type, format, or boolean flags in this discovery step.

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
2. **description** - The attribute description as defined above.
3. **sequences** - An array of objects, each containing:
   - **start_sequence** - The exact opening phrase, start of the sequence - marker from the documentation (word-for-word, searchable)
   - **end_sequence** - The exact closing phrase, end of the sequence - marker from the documentation (word-for-word, searchable)
   - Do NOT include chunk id. The system already knows which chunk is being processed.
                                
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
  - Never include `chunk_id`/`chunkId` in marker objects.
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

_ATTRIBUTE_EVIDENCE_RULES = textwrap.dedent("""
Evidence rules:
- Use only evidence about this attribute of the given object class. The object class's own response representation and its own create and update requests (request bodies, request schemas or parameters, whatever they are named) all describe this object class. Ignore same-named attributes of other object classes.
- A field counts as this attribute only under the same name; a differently named request field (e.g. `enabled` for an attribute named `is_enabled`) is a different attribute.
- Evidence is what the relevant sequences state or what follows clearly from the documentation's own conventions: schema keywords (e.g. readOnly, writeOnly, required, array types, references), field or property tables (type, constraints, access or supported operations), request and response schemas and examples, and conditions in descriptions.
- Evaluate from the perspective of the privileged integration account the connector runs under (administrator or global management permissions). A condition that such an account satisfies does not restrict a value; a condition that applies to every caller (e.g. a value that exists only on already created objects) does.
- Return null only when the evidence neither states nor lets you derive a value. Never guess, and never use knowledge about the application from outside the provided evidence.
""")

get_build_type_format_from_sequences_system_prompt = (
    textwrap.dedent("""
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a compact attribute context with:
  - name
  - description
  - current type/format values when already known
  - evidence sequences with source text

Your task is to find ONLY the attribute `type` and `format` using the provided relevant sequences as evidence. Each sequence includes a start and end marker that corresponds to a specific section of the documentation.
Decide `type` and `format` by their definitions in the output schema below.
                                                         
Your secondary task is to verify the existing non-null `type` and `format` and correct them if there is clear and irrefutable evidence in the relevant sequences that they are wrong.
However, with this second task be very conservative in making corrections. Only change existing non-null values if the evidence is overwhelmingly clear and unambiguous.
                                                         
Rules:
- Fill only `type` and `format`.
- Keep `description` and all boolean flags unchanged.
- For `type` and `format` currently null or missing, fill them when the evidence determines them.
- For `type` and `format` currently non-null, only change it if the relevant sequences provide overwhelmingly clear and irrefutable evidence that it is incorrect.
- If the evidence is unclear or contradictory, keep the field null or unchanged.

Hard constraints:
  - Do NOT invent data.
  - Do NOT fill or change description or boolean flags.
  - Return only a partial JSON object with these fields: `type`, `format`.
  - Do NOT return `name`, `description`, boolean flags, `relevant_sequences`, or any other attribute fields.
  - If type/format cannot be improved, keep existing values when present; otherwise return null for unknown values.
""")
    + _ATTRIBUTE_EVIDENCE_RULES
)

get_build_type_format_from_sequences_user_prompt = textwrap.dedent("""
Object Class: {object_class}

<attribute_context>
{attribute_context}
</attribute_context>

Find only the type and format the evidence determines.
You can also correct existing non-null type/format values if there is overwhelming evidence that they are wrong.

Return the json object based on format instructions.
""")

get_build_boolean_flags_from_sequences_system_prompt = (
    textwrap.dedent("""
You are an expert IGA/IDM analyst. You will be given:
- an object class name (e.g. "User", "Group", ...)
- a compact attribute context with:
  - name
  - type
  - format
  - description
  - current boolean values when already known
  - evidence sequences with source text

Your task is to find ONLY boolean attribute values using the provided relevant sequences as evidence.
Decide each flag by its definition in the output schema below.

Rules:
- Fill only these boolean fields: `mandatory`, `updatable`, `creatable`, `readable`, `multivalue`, `returnedByDefault`.
- Keep `type`, `format`, and `description` unchanged.
- For each boolean field currently null or missing, fill it when the evidence determines it.
- For each boolean field currently non-null, only change it if the relevant sequences provide overwhelmingly clear and irrefutable evidence that it is incorrect.

Hard constraints:
  - Do NOT invent data.
  - Do NOT fill or change type, format, or description.
  - Return only a partial JSON object with these fields: `mandatory`, `updatable`, `creatable`, `readable`, `multivalue`, `returnedByDefault`.
  - Do NOT return `name`, `type`, `format`, `description`, `relevant_sequences`, or any other attribute fields.
  - If boolean flags cannot be improved, keep existing values when present; otherwise return null for unknown values.
""")
    + _ATTRIBUTE_EVIDENCE_RULES
)

get_build_boolean_flags_from_sequences_user_prompt = textwrap.dedent("""
Object Class: {object_class}

<attribute_context>
{attribute_context}
</attribute_context>

Find only the boolean attribute values the evidence determines.
You can also correct existing non-null boolean values if there is overwhelming evidence that they are wrong.

Return the json object based on format instructions.
""")

get_consolidate_attributes_system_prompt = (
    textwrap.dedent("""
You are an expert IGA/IDM analyst specializing in consolidating and refining API attribute information.
You will be given:
- an object class name (e.g. "User", "Group", ...)
- a compact attribute context with:
  - name
  - type
  - format
  - description
  - known boolean flags
  - evidence sequences with source text

Your primary task is to review the provided attribute information and produce a consolidated and refined version of it, ensuring that all fields are as accurate as possible based on the provided evidence in the relevant sequences.                                                           

Rules:
- Decide every field by its definition in the output schema below.
- Fill a field that is currently null when the combined evidence determines it.
- Be very conservative in making any changes to the existing non-null values. Only change them if the relevant sequences provide overwhelmingly clear and irrefutable evidence that they are incorrect.

Hard constraints:
- Do NOT invent data.
- If nothing can be improved, return the attributes exactly as received.
""")
    + _ATTRIBUTE_EVIDENCE_RULES
)

get_consolidate_attributes_user_prompt = textwrap.dedent("""
Object Class: {object_class}
                                                         
<attribute_context>
{attribute_context}
</attribute_context>
                                                         
Review the provided attribute information and produce a consolidated and refined version of it, ensuring that all fields are as accurate as possible based on the provided evidence in the relevant sequences.
Fill null fields only when the evidence determines them; change non-null values only on overwhelming evidence.
Return the json object based on format instructions.
""")
