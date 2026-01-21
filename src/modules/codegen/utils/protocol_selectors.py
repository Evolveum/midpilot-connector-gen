#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

"""
Helpers that select prompts and docs based on API protocol.
"""

from typing import List, Tuple

from ..prompts.rest.createPrompts import get_create_system_prompt, get_create_user_prompt
from ..prompts.rest.deletePrompts import get_delete_system_prompt, get_delete_user_prompt
from ..prompts.rest.searchPrompts import get_search_system_prompt, get_search_user_prompt
from ..prompts.rest.updatePrompts import get_update_system_prompt, get_update_user_prompt
from ..prompts.scim.scimCreatePrompts import get_scim_create_system_prompt, get_scim_create_user_prompt
from ..prompts.scim.scimDeletePrompts import get_scim_delete_system_prompt, get_scim_delete_user_prompt
from ..prompts.scim.scimSearchPrompts import get_scim_search_system_prompt, get_scim_search_user_prompt
from ..prompts.scim.scimUpdatePrompts import get_scim_update_system_prompt, get_scim_update_user_prompt
from .api_type_helper import is_scim_api

PromptPair = Tuple[str, str]

PROMPT_MAP = {
    "search": {
        "rest": (get_search_system_prompt, get_search_user_prompt),
        "scim": (get_scim_search_system_prompt, get_scim_search_user_prompt),
    },
    "create": {
        "rest": (get_create_system_prompt, get_create_user_prompt),
        "scim": (get_scim_create_system_prompt, get_scim_create_user_prompt),
    },
    "update": {
        "rest": (get_update_system_prompt, get_update_user_prompt),
        "scim": (get_scim_update_system_prompt, get_scim_update_user_prompt),
    },
    "delete": {
        "rest": (get_delete_system_prompt, get_delete_user_prompt),
        "scim": (get_scim_delete_system_prompt, get_scim_delete_user_prompt),
    },
}

DOCS_MAP = {
    "search": {"rest": "rest/40-search-users.adoc", "scim": "scim/40-search-users.adoc"},
    "create": {"rest": "rest/50-create.adoc", "scim": "scim/50-create.adoc"},
    "update": {"rest": "rest/60-update.adoc", "scim": "scim/60-update.adoc"},
    "delete": {"rest": "rest/70-delete.adoc", "scim": "scim/70-delete.adoc"},
}


def select_prompts_for_protocol(operation: str, api_types: List[str]) -> PromptPair:
    """
    Select appropriate system and user prompts based on API protocol type.
    """
    protocol = "scim" if is_scim_api(api_types) else "rest"
    prompts = PROMPT_MAP.get(operation, {}).get(protocol)

    if not prompts:
        raise ValueError(f"No prompts found for operation '{operation}' with protocol '{protocol}'")

    return prompts


def select_docs_path_for_protocol(operation: str, api_types: List[str]) -> str:
    """
    Select appropriate documentation file path based on API protocol type.
    """
    protocol = "scim" if is_scim_api(api_types) else "rest"
    return DOCS_MAP.get(operation, {}).get(protocol, f"{operation}.adoc")
