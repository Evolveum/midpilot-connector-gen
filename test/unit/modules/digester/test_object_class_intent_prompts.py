# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Object-class detection reads a business-domain lens (`GenerationIntent`) that changes
what extraction and ranking prompts treat as primary, without changing which
documentation is read. These tests prove the intent-specific prompt text actually
differs, that the correct (single) profile is selected per call rather than every
profile being concatenated together, and that every `GenerationIntent` member has a
registered profile.
"""

import pytest

from src.modules.digester.prompts.object_class_intents import get_intent_profile
from src.modules.digester.prompts.rest.object_class_prompts import get_object_class_system_prompt
from src.modules.digester.prompts.rest.object_class_relevancy_prompt import (
    get_object_classes_relevancy_system_prompt,
)
from src.modules.digester.prompts.rest.sorting_output_prompts import sort_object_classes_system_prompt
from src.modules.digester.prompts.scim.object_class_prompts import scim_object_class_system_prompt
from src.modules.digester.prompts.sql.object_class_ranking_prompts import sort_sql_object_classes_system_prompt
from src.shared.enums import GenerationIntent


def test_every_generation_intent_has_a_registered_profile():
    """A future intent added to the enum without a profile should fail loudly here."""
    for intent in GenerationIntent:
        profile = get_intent_profile(intent)
        assert profile.persona_domain
        assert profile.rest_extraction_buckets
        assert profile.scim_custom_guidance
        assert profile.confidence_high
        assert profile.confidence_medium
        assert profile.confidence_low
        assert profile.ranking_signals
        assert profile.sql_ranking_signals


def test_get_intent_profile_rejects_unregistered_value():
    with pytest.raises(KeyError):
        get_intent_profile("bogus-intent")  # type: ignore[arg-type]


class TestRestExtractionPrompt:
    def test_defaults_to_management(self):
        assert get_object_class_system_prompt() == get_object_class_system_prompt(GenerationIntent.MANAGEMENT)

    def test_management_and_itsm_prompts_differ(self):
        management = get_object_class_system_prompt(GenerationIntent.MANAGEMENT)
        itsm = get_object_class_system_prompt(GenerationIntent.ITSM)
        assert management != itsm

    def test_management_prompt_only_carries_management_vocabulary(self):
        management = get_object_class_system_prompt(GenerationIntent.MANAGEMENT)
        assert "Entitlement" in management
        assert "Ticket" not in management
        assert "Incident" not in management

    def test_itsm_prompt_carries_itsm_vocabulary_and_still_permits_identity_classes(self):
        itsm = get_object_class_system_prompt(GenerationIntent.ITSM)
        assert "Ticket" in itsm
        assert "incident" in itsm
        # per product decision: itsm intent must not exclude identity/management classes
        # from extraction - only ranking treats them as lower priority.
        assert "Identity / Organization" in itsm

    def test_combined_prompt_carries_both_domains(self):
        combined = get_object_class_system_prompt(GenerationIntent.MANAGEMENT_ITSM)
        assert "Identity / User" in combined
        assert "Role / Entitlement" in combined
        assert "Ticket / Issue / Case / Work Package" in combined
        assert "workPackage" in combined


class TestScimExtractionPrompt:
    def test_defaults_to_management(self):
        assert scim_object_class_system_prompt() == scim_object_class_system_prompt(GenerationIntent.MANAGEMENT)

    def test_management_and_itsm_prompts_differ(self):
        management = scim_object_class_system_prompt(GenerationIntent.MANAGEMENT)
        itsm = scim_object_class_system_prompt(GenerationIntent.ITSM)
        assert management != itsm
        assert "Ticket" in itsm
        assert "Ticket" not in management

    def test_json_examples_survive_intent_templating(self):
        """Guards against the f-string/LangChain double-templating brace bug."""
        for intent in GenerationIntent:
            rendered = scim_object_class_system_prompt(intent)
            assert '"name": "SlackUserExtension"' in rendered
            assert '"name": "Application"' in rendered
            # no unresolved single-brace template markers should leak into prompt text
            assert "{name}" not in rendered
            assert "{schemaUrn}" not in rendered

    def test_combined_prompt_carries_both_domains(self):
        combined = scim_object_class_system_prompt(GenerationIntent.MANAGEMENT_ITSM)
        assert "Application, License, Role, Entitlement" in combined
        assert "Ticket, Case, Incident, ChangeRequest, WorkPackage" in combined


class TestConfidenceRelevancyPrompt:
    def test_defaults_to_management(self):
        assert get_object_classes_relevancy_system_prompt() == get_object_classes_relevancy_system_prompt(
            intent=GenerationIntent.MANAGEMENT
        )

    def test_management_and_itsm_criteria_differ(self):
        management = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.MANAGEMENT)
        itsm = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.ITSM)
        assert management != itsm

    def test_itsm_ranks_tickets_high_and_identity_classes_low(self):
        itsm = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.ITSM)
        high_section = itsm.split("2) MEDIUM")[0]
        low_section = itsm.split("3) LOW")[1]
        assert "Ticket" in high_section
        assert "User" in low_section or "Organization" in low_section

    def test_management_ranks_identity_classes_high(self):
        management = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.MANAGEMENT)
        high_section = management.split("2) MEDIUM")[0]
        assert "User" in high_section
        assert "Role" in high_section

    def test_single_domain_work_package_confidence_rules(self):
        management = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.MANAGEMENT)
        itsm = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.ITSM)
        assert "WorkPackage" in management.split("3) LOW")[1]
        assert "WorkPackage" in itsm.split("2) MEDIUM")[0]

    def test_combined_ranks_management_and_itsm_core_classes_high(self):
        combined = get_object_classes_relevancy_system_prompt(intent=GenerationIntent.MANAGEMENT_ITSM)
        high_section, remainder = combined.split("2) MEDIUM", maxsplit=1)
        low_section = remainder.split("3) LOW", maxsplit=1)[1]

        for object_class in ("User", "Role", "Ticket", "WorkPackage"):
            assert object_class in high_section
        assert "Union rule" in high_section
        assert "core identity/access class" in low_section
        assert "core service-management work-item" in low_section

    def test_compact_output_contract_still_applies_regardless_of_intent(self):
        compact = get_object_classes_relevancy_system_prompt(compact_output=True, intent=GenerationIntent.ITSM)
        assert "do not copy descriptions" in compact.lower()


class TestSortingPrompts:
    def test_rest_scim_sorting_defaults_to_management(self):
        assert sort_object_classes_system_prompt() == sort_object_classes_system_prompt(GenerationIntent.MANAGEMENT)

    def test_rest_scim_sorting_differs_by_intent(self):
        management = sort_object_classes_system_prompt(GenerationIntent.MANAGEMENT)
        itsm = sort_object_classes_system_prompt(GenerationIntent.ITSM)
        assert management != itsm
        assert "service-management workflow" in itsm
        assert "identity & access" in management

    def test_sql_sorting_defaults_to_management(self):
        assert sort_sql_object_classes_system_prompt() == sort_sql_object_classes_system_prompt(
            GenerationIntent.MANAGEMENT
        )

    def test_sql_sorting_differs_by_intent(self):
        management = sort_sql_object_classes_system_prompt(GenerationIntent.MANAGEMENT)
        itsm = sort_sql_object_classes_system_prompt(GenerationIntent.ITSM)
        assert management != itsm
        assert "ITSM work-item entities" in itsm
        assert "IGA/IDM entities" in management

    def test_combined_sorting_keeps_both_high_and_management_first(self):
        rest_scim = sort_object_classes_system_prompt(GenerationIntent.MANAGEMENT_ITSM)
        sql = sort_sql_object_classes_system_prompt(GenerationIntent.MANAGEMENT_ITSM)

        assert "Keep both management and ITSM core resources in the HIGH bucket" in rest_scim
        assert "rank management resources first" in rest_scim
        assert "Keep both management and ITSM core entities in the high-confidence bucket" in sql
        assert "put management entities first" in sql
