# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Helpers that select prompts and docs based on API protocol.
"""

from typing import Mapping

from src.common.enums import ApiType
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.prompts.rest.create_prompts import get_create_system_prompt, get_create_user_prompt
from src.modules.codegen.prompts.rest.delete_prompts import get_delete_system_prompt, get_delete_user_prompt
from src.modules.codegen.prompts.rest.search_prompts import get_search_system_prompt, get_search_user_prompt
from src.modules.codegen.prompts.rest.update_prompts import get_update_system_prompt, get_update_user_prompt
from src.modules.codegen.prompts.scim.create_prompts import get_scim_create_system_prompt, get_scim_create_user_prompt
from src.modules.codegen.prompts.scim.delete_prompts import get_scim_delete_system_prompt, get_scim_delete_user_prompt
from src.modules.codegen.prompts.scim.search_prompts import get_scim_search_system_prompt, get_scim_search_user_prompt
from src.modules.codegen.prompts.scim.update_prompts import get_scim_update_system_prompt, get_scim_update_user_prompt
from src.modules.codegen.schema import OperationAssets

PROMPT_MAP: Mapping[str, Mapping[ApiType, OperationAssets]] = {
    "search": {
        ApiType.REST: OperationAssets(get_search_system_prompt, get_search_user_prompt, "rest/40-search-users.adoc"),
        ApiType.SCIM: OperationAssets(
            get_scim_search_system_prompt, get_scim_search_user_prompt, "scim/40-search.adoc"
        ),
    },
    "create": {
        ApiType.REST: OperationAssets(get_create_system_prompt, get_create_user_prompt, "rest/50-create.adoc"),
        ApiType.SCIM: OperationAssets(
            get_scim_create_system_prompt, get_scim_create_user_prompt, "scim/50-create.adoc"
        ),
    },
    "update": {
        ApiType.REST: OperationAssets(get_update_system_prompt, get_update_user_prompt, "rest/60-update.adoc"),
        ApiType.SCIM: OperationAssets(
            get_scim_update_system_prompt, get_scim_update_user_prompt, "scim/60-update.adoc"
        ),
    },
    "delete": {
        ApiType.REST: OperationAssets(get_delete_system_prompt, get_delete_user_prompt, "rest/70-delete.adoc"),
        ApiType.SCIM: OperationAssets(
            get_scim_delete_system_prompt, get_scim_delete_user_prompt, ""
        ),  # TODO add new documentation for SCIM delete operation
    },
    "native_schema": {
        ApiType.REST: OperationAssets(
            get_native_schema_system_prompt, get_native_schema_user_prompt, "rest/25-user-schema.adoc"
        ),
        ApiType.SCIM: OperationAssets(
            get_native_schema_system_prompt, get_native_schema_user_prompt, "scim/25-schema-customization.adoc"
        ),
    },
}


def get_operation_assets(operation: str, protocol: ApiType) -> OperationAssets:
    op = operation.lower()
    if op not in PROMPT_MAP or protocol not in PROMPT_MAP[op]:
        raise ValueError(f"Unsupported operation/protocol: {operation}/{protocol}")
    return PROMPT_MAP[op][protocol]
