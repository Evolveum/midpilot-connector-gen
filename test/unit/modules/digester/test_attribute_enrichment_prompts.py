# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.modules.digester.prompts.rest.attributes_prompts import (
    _ATTRIBUTE_EVIDENCE_RULES,
    get_build_boolean_flags_from_sequences_system_prompt,
    get_build_boolean_flags_from_sequences_user_prompt,
    get_build_type_format_from_sequences_system_prompt,
    get_build_type_format_from_sequences_user_prompt,
    get_consolidate_attributes_system_prompt,
    get_consolidate_attributes_user_prompt,
)
from src.modules.digester.schemas import (
    AttributeBooleanFlagsBase,
    AttributeBooleanFlagsBuildResponse,
    AttributeTypeFormatBase,
    AttributeTypeFormatBuildResponse,
)

_ENRICHMENT_PROMPTS = [
    (get_build_type_format_from_sequences_system_prompt, get_build_type_format_from_sequences_user_prompt),
    (get_build_boolean_flags_from_sequences_system_prompt, get_build_boolean_flags_from_sequences_user_prompt),
    (get_consolidate_attributes_system_prompt, get_consolidate_attributes_user_prompt),
]


@pytest.mark.parametrize(
    ("llm_response_model", "api_model"),
    [
        (AttributeBooleanFlagsBuildResponse, AttributeBooleanFlagsBase),
        (AttributeTypeFormatBuildResponse, AttributeTypeFormatBase),
    ],
)
def test_enrichment_responses_share_field_definitions_with_the_api_model(llm_response_model, api_model):
    for name, field in llm_response_model.model_fields.items():
        assert field.description
        assert field.description == api_model.model_fields[name].description


@pytest.mark.parametrize(("system_prompt", "user_prompt"), _ENRICHMENT_PROMPTS)
def test_enrichment_prompts_carry_the_shared_evidence_rules(system_prompt, user_prompt):
    assert system_prompt.count(_ATTRIBUTE_EVIDENCE_RULES) == 1


@pytest.mark.parametrize(("system_prompt", "user_prompt"), _ENRICHMENT_PROMPTS)
def test_enrichment_prompts_take_only_the_extractor_inputs(system_prompt, user_prompt):
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("user", user_prompt)])

    assert set(prompt.input_variables) == {"object_class", "attribute_context"}
