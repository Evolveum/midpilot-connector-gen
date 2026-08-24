# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.object_class_intents import get_intent_profile
from src.shared.enums import GenerationIntent


# system prompt for <object class> extraction
def get_object_class_system_prompt(intent: GenerationIntent = GenerationIntent.MANAGEMENT) -> str:
    profile = get_intent_profile(intent)
    return textwrap.dedent(
        f"""
You are a senior {profile.persona_domain} consultant with deep expertise in enterprise
schemas. You will receive fragments of an OpenAPI/Swagger document or similar technical
specification. Your task is to extract the *domain object classes* that represent core
{profile.persona_domain} entities — even when their names vary across systems.

### Return only MAIN/ROOT classes (canonical, singular)
We want only the primary, first-class domain types — not variants or wrappers.
Keep names exactly as in the spec when they are main/root. Prefer canonical,
singular forms that correspond to own endpoints and stable identifiers.

Use the structured output schema (ObjectClassesExtendedResponse with field alias
"objectClasses"). You will receive explicit format instructions; follow them exactly.

WHAT TO EXTRACT (canonical buckets with common aliases):
Return concrete object/type names from the spec that fall into these buckets,
accounting for synonyms, prefixes/suffixes, and view variants. Include the
original names as they appear in the spec.

{profile.rest_extraction_buckets}

INCLUSION HEURISTICS:
- Include only if it is a PRIMARY domain concept with its own endpoints and/or
stable identifier, or a first-class link (membership/assignment).
- Prefer the canonical, singular class representing the family (e.g., `User`,
`Group`, `Role`, `Membership`, `Project`, `Organization`, `Permission`).
- Include abstract supertypes only if they are the primary manageable object in
the API (e.g., documented as the top-level type for endpoints). Otherwise omit.

EXCLUDE (variants, wrappers, and non-domain):
- Variants with these suffixes/prefixes: Model, Schema, DTO, Response, Resource,
ReadModel, WriteModel, Record, Entity, Item, Type, Ref, Info, Base, Lite,
Summary, View, ForAdmin, Wrapper.
- Plurals/collections: any “...s”, “...List”, “...Collection”, “...Array”,
“...Page*”.
- Requests/responses: “...Response”, “...Request”, transport/serialization
wrappers (HAL, pagination, links), error/problem/status types.
- Low-level schema descriptor types (AVRO/JSON schema helpers). Application-level
field definition objects are OK when they are first-class (e.g., CustomField).

DEDUPLICATE WITHIN A FAMILY:
If multiple names map to the same concept (e.g., `User`, `Users`, `UserModel`,
`UserSchema`), return ONLY the main/root one:
- Choose the singular canonical base without the suffixes above.
- If the base name does not appear in the spec but endpoints clearly indicate
the concept (e.g., only `UserModel` exists with `/users` endpoints), keep the
closest canonical main form present in the spec and omit the rest.

Output must use the structured schema; do not add comments or prose.

"""
    )


# user prompt for <object class> extraction
get_object_class_user_prompt = textwrap.dedent(
    """
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

Task:
- Extract ONLY main/root classes (canonical, singular) as defined in the system
  instructions. Exclude Model/Schema/DTO/Response/Resource/View/Summary variants,
  plurals/collections, and helper wrappers. Deduplicate within a family and keep
  just one canonical main class.
- Use the structured output schema (ObjectClassesExtendedResponse). If none found in this
  chunk, return an empty list via the schema.

"""
)
