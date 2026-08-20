# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.object_class_intents import get_intent_profile
from src.shared.enums import GenerationIntent


def sort_sql_object_classes_system_prompt(intent: GenerationIntent = GenerationIntent.MANAGEMENT) -> str:
    profile = get_intent_profile(intent)
    return textwrap.dedent(
        f"""
    You are ranking database-backed object classes by {profile.persona_domain} primacy
    within one confidence bucket. Each input contains only its exact name and a compact
    description used as a ranking hint.

    {profile.sql_ranking_signals} If uncertain, keep the original relative order.

    Return every exact input name once in the requested order. Do not return
    descriptions or invent, edit, merge, or drop names. Output only the
    structured response.
    """
    )


sort_sql_object_classes_user_prompt = textwrap.dedent(
    """
    Confidence bucket: {confidence_level}

    <objectClasses>
    {items_json}
    </objectClasses>

    Return only the ordered object-class names using the structured schema.
    """
)
