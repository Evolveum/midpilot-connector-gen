# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.object_class_intents import get_intent_profile
from src.shared.enums import GenerationIntent


def get_object_classes_relevancy_system_prompt(
    *,
    compact_output: bool = False,
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> str:
    profile = get_intent_profile(intent)
    output_contract = (
        "Return only each exact `name` with its `confidence`; do not copy descriptions into the output."
        if compact_output
        else "Preserve both `name` and `description` exactly as provided and add `confidence`."
    )
    semantic_rules = (
        "- Use object name semantics first and the compact description second."
        if compact_output
        else "- Use object name semantics first, description second, chunk count as a weak tie-breaker only."
    )
    return textwrap.dedent(
        f"""
You are an expert {profile.persona_domain} Integration Specialist.
Your task is to assign a confidence level to each provided API object class based on
practical importance for this integration.

Return EVERY provided object class exactly once with one confidence value:
- high
- medium
- low

Do NOT remove, merge, or invent classes.
{output_contract}

Confidence criteria:

{profile.confidence_high}

{profile.confidence_medium}

{profile.confidence_low}

Rules:
{semantic_rules}
- If uncertain, choose LOWER confidence.
- Prefer canonical singular forms over technical variants when both appear.

Output only structured JSON per format instructions.
"""
    )


def get_object_classes_relevancy_user_prompt(
    object_classes_json: str,
    *,
    source_description: str = "API documentation",
    intent: GenerationIntent = GenerationIntent.MANAGEMENT,
) -> str:
    profile = get_intent_profile(intent)
    return textwrap.dedent(
        f"""
You are provided with object classes extracted from {source_description}.
Assign confidence to each class for {profile.persona_domain} integration use.

Input:
<objectClasses>
{object_classes_json}
</objectClasses>
"""
    )
