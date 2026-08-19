# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.object_class_intents import get_intent_profile
from src.shared.enums import GenerationIntent


# Sorting object classes
def sort_object_classes_system_prompt(intent: GenerationIntent = GenerationIntent.MANAGEMENT) -> str:
    profile = get_intent_profile(intent)
    return textwrap.dedent(f"""
    <instruction>
    You are ranking extracted domain object classes by **{profile.persona_domain} primacy**
    within one confidence bucket.
    You receive:
      - A list of object classes (already deduplicated) that all share the same confidence bucket.
      - Each item includes: name, description, superclass, abstract, embedded, relevant, confidence.

    Rank the list so that the most central, frequently-referenced, and first-class
    entities for this intent come first. Use these signals:

    {profile.ranking_signals}

    Use the structured output schema (ObjectClassesRankedResponse with field alias 'objectClasses').
    Do not edit, invent, or drop items—only reorder the same set.
    No comments or prose.
    </instruction>
""")


sort_object_classes_user_prompt = textwrap.dedent("""
    Confidence bucket: {confidence_level}

    Extracted object classes from previous LLM call:
    <items>
    {items_json}
    </items>

    Task:
    - Return the same items reordered by relevance using the structured output schema (ObjectClassesRankedResponse).
    - Do not add/remove/modify fields; only change order.
""")

# Sorting Auth
sort_auth_system_prompt = textwrap.dedent("""
    <instruction>
    You are ranking authentication mechanisms by **practical relevance and primacy**
    as implied by the docs. You receive:
    - A list of auth mechanisms (name, type, quirks) already deduplicated.

    Ranking guidelines:
    - Follow explicit guidance in the docs (e.g., "use OAuth 2.0", "preferred", "recommended").
    - If no explicit guidance, infer from modern common practice and scope coverage
      (e.g., OAuth2 flows that cover most endpoints > bearer tokens > basic/session),
      but DO NOT invent mechanisms that are not mentioned.
    - Prefer mechanisms documented as secure, comprehensive, and widely applicable.
    - If ties remain, keep original relative order.

    Use the structured output schema.
    Do not edit, invent, or drop items—only reorder the same set.
    No comments or prose.
    </instruction>
""")

sort_auth_user_prompt = textwrap.dedent("""
    Extracted authentication mechanisms from previous LLM call:
    
    <items>
    {items_json}
    </items>
    
    Task:
    - Return the same items reordered by relevance using the structured output schema.
    - Do not add/remove/modify fields; only change order.
""")
