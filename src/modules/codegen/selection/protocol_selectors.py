# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Helpers that select prompts and docs based on API protocol.
"""

from dataclasses import dataclass
from typing import Mapping

from ..prompts.rest.create_prompts import get_create_system_prompt, get_create_user_prompt
from ..prompts.rest.delete_prompts import get_delete_system_prompt, get_delete_user_prompt
from ..prompts.rest.search_prompts import get_search_system_prompt, get_search_user_prompt
from ..prompts.rest.update_prompts import get_update_system_prompt, get_update_user_prompt
from ..prompts.scim.scim_create_prompts import get_scim_create_system_prompt, get_scim_create_user_prompt
from ..prompts.scim.scim_delete_prompts import get_scim_delete_system_prompt, get_scim_delete_user_prompt
from ..prompts.scim.scim_search_prompts import get_scim_search_system_prompt, get_scim_search_user_prompt
from ..prompts.scim.scim_update_prompts import get_scim_update_system_prompt, get_scim_update_user_prompt
from .protocol import ApiProtocol


@dataclass(frozen=True)
class OperationAssets:
    system_prompt: str
    user_prompt: str
    docs_path: str


PROMPT_MAP: Mapping[str, Mapping[ApiProtocol, OperationAssets]] = {
    "search": {
        ApiProtocol.REST: OperationAssets(
            get_search_system_prompt, get_search_user_prompt, "rest/40-search-users.adoc"
        ),
        ApiProtocol.SCIM: OperationAssets(
            get_scim_search_system_prompt, get_scim_search_user_prompt, "scim/40-search-users.adoc"
        ),
    },
    "create": {
        ApiProtocol.REST: OperationAssets(get_create_system_prompt, get_create_user_prompt, "rest/50-create.adoc"),
        ApiProtocol.SCIM: OperationAssets(
            get_scim_create_system_prompt, get_scim_create_user_prompt, "scim/50-create.adoc"
        ),
    },
    "update": {
        ApiProtocol.REST: OperationAssets(get_update_system_prompt, get_update_user_prompt, "rest/60-update.adoc"),
        ApiProtocol.SCIM: OperationAssets(
            get_scim_update_system_prompt, get_scim_update_user_prompt, "scim/60-update.adoc"
        ),
    },
    "delete": {
        ApiProtocol.REST: OperationAssets(get_delete_system_prompt, get_delete_user_prompt, "rest/70-delete.adoc"),
        ApiProtocol.SCIM: OperationAssets(
            get_scim_delete_system_prompt, get_scim_delete_user_prompt, "scim/70-delete.adoc"
        ),
    },
}


def get_operation_assets(operation: str, protocol: ApiProtocol) -> OperationAssets:
    op = operation.lower()
    if op not in PROMPT_MAP or protocol not in PROMPT_MAP[op]:
        raise ValueError(f"Unsupported operation/protocol: {operation}/{protocol}")
    return PROMPT_MAP[op][protocol]
