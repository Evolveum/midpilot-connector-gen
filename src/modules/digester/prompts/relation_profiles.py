# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Immutable protocol prompt profiles for the shared relation pipeline."""

from dataclasses import dataclass
from typing import Protocol

from src.modules.digester.prompts.rest import relations_prompts as rest
from src.modules.digester.prompts.scim import relations_prompts as scim
from src.modules.digester.prompts.sql import relations_prompts as sql
from src.shared.enums import ApiType


@dataclass(frozen=True)
class RelationPromptSet:
    """All prompts needed by one execution of the staged relation pipeline."""

    protocol: ApiType
    harvest_system: str
    harvest_user: str
    class_sweep_system: str
    class_sweep_user: str
    pair_focus_system: str
    pair_focus_user: str
    adjudication_system: str
    adjudication_user: str
    verification_system: str
    verification_user: str


class _RelationSystemPromptModule(Protocol):
    get_relation_harvest_system_prompt: str
    get_relation_class_sweep_system_prompt: str
    get_relation_pair_focus_system_prompt: str
    get_relation_adjudication_system_prompt: str
    get_relation_verification_system_prompt: str


def _profile(protocol: ApiType, system_prompts: _RelationSystemPromptModule) -> RelationPromptSet:
    """Combine protocol-specific reasoning with the shared stage input contract."""
    return RelationPromptSet(
        protocol=protocol,
        harvest_system=system_prompts.get_relation_harvest_system_prompt,
        harvest_user=rest.get_relation_harvest_user_prompt,
        class_sweep_system=system_prompts.get_relation_class_sweep_system_prompt,
        class_sweep_user=rest.get_relation_class_sweep_user_prompt,
        pair_focus_system=system_prompts.get_relation_pair_focus_system_prompt,
        pair_focus_user=rest.get_relation_pair_focus_user_prompt,
        adjudication_system=system_prompts.get_relation_adjudication_system_prompt,
        adjudication_user=rest.get_relation_adjudication_user_prompt,
        verification_system=system_prompts.get_relation_verification_system_prompt,
        verification_user=rest.get_relation_verification_user_prompt,
    )


_PROMPT_SETS = {
    ApiType.REST: _profile(ApiType.REST, rest),
    ApiType.SCIM: _profile(ApiType.SCIM, scim),
    ApiType.SQL: _profile(ApiType.SQL, sql),
}


def get_relation_prompt_set(protocol: ApiType) -> RelationPromptSet:
    """Return the complete prompt set for ``protocol`` without implicit fallback."""
    return _PROMPT_SETS[protocol]
