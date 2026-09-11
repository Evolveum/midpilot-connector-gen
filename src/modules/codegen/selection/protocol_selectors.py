# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Helpers that select prompts and docs based on API protocol.
"""

from typing import Mapping

from src.modules.codegen.enums import ArtifactKind, SearchIntent
from src.modules.codegen.prompts.authorization_prompts import (
    get_authorization_system_prompt,
    get_authorization_user_prompt,
)
from src.modules.codegen.prompts.native_schema_prompts import (
    get_native_schema_system_prompt,
    get_native_schema_user_prompt,
)
from src.modules.codegen.prompts.rest.create_prompts import get_create_system_prompt, get_create_user_prompt
from src.modules.codegen.prompts.rest.delete_prompts import get_delete_system_prompt, get_delete_user_prompt
from src.modules.codegen.prompts.rest.search_prompts import (
    get_search_all_system_prompt,
    get_search_filter_system_prompt,
    get_search_id_system_prompt,
    get_search_user_prompt,
)
from src.modules.codegen.prompts.rest.update_prompts import get_update_system_prompt, get_update_user_prompt
from src.modules.codegen.prompts.scim.create_prompts import get_scim_create_system_prompt, get_scim_create_user_prompt
from src.modules.codegen.prompts.scim.delete_prompts import get_scim_delete_system_prompt, get_scim_delete_user_prompt
from src.modules.codegen.prompts.scim.native_schema_prompts import (
    get_scim_native_schema_system_prompt,
    get_scim_native_schema_user_prompt,
)
from src.modules.codegen.prompts.scim.search_prompts import (
    get_scim_search_all_system_prompt,
    get_scim_search_filter_system_prompt,
    get_scim_search_id_system_prompt,
    get_scim_search_user_prompt,
)
from src.modules.codegen.prompts.scim.update_prompts import get_scim_update_system_prompt, get_scim_update_user_prompt
from src.modules.codegen.prompts.sql.create_prompts import get_sql_create_system_prompt, get_sql_create_user_prompt
from src.modules.codegen.prompts.sql.delete_prompts import get_sql_delete_system_prompt, get_sql_delete_user_prompt
from src.modules.codegen.prompts.sql.native_schema_prompts import (
    get_sql_native_schema_system_prompt,
    get_sql_native_schema_user_prompt,
)
from src.modules.codegen.prompts.sql.search_prompts import (
    get_sql_search_all_system_prompt,
    get_sql_search_filter_system_prompt,
    get_sql_search_id_system_prompt,
    get_sql_search_user_prompt,
)
from src.modules.codegen.prompts.sql.update_prompts import get_sql_update_system_prompt, get_sql_update_user_prompt
from src.modules.codegen.schema import OperationAssets
from src.shared.enums import ApiType

# The ConnID mapping is part of the native schema and shares its script, so this
# reference is attached to every ``native_schema`` asset rather than owning a
# PROMPT_MAP entry. It explains *which* native attribute belongs to *which* ConnID
# built-in; the protocol's own schema document remains the authority on syntax.
# Protocol-neutral, hence the documentations root rather than ``rest/``.
CONNID_ATTRIBUTES_DOCS_PATH = "connid-attributes.adoc"

# The declarative-YAML reference every operation carries alongside its own docs_path, so the
# LLM can choose YAML over Groovy whenever the declarative format documents enough coverage.
# REST and SCIM share one framework (SCIMREST) and therefore one manifest/schema/operation YAML
# shape, bundled once at the documentations root next to CONNID_ATTRIBUTES_DOCS_PATH. SQL is a
# separate framework with its own YAML shape.
SCIM_REST_DECLARATIVE_DOCS_PATH = "declarative-yaml.adoc"
SQL_DECLARATIVE_DOCS_PATH = "sql/declarative-yaml.adoc"


PROMPT_MAP: Mapping[str, Mapping[ApiType, OperationAssets]] = {
    "create": {
        ApiType.REST: OperationAssets(
            get_create_system_prompt,
            get_create_user_prompt,
            "rest/50-create.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SCIM: OperationAssets(
            get_scim_create_system_prompt,
            get_scim_create_user_prompt,
            "scim/50-create.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SQL: OperationAssets(
            get_sql_create_system_prompt,
            get_sql_create_user_prompt,
            "sql/create.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
    },
    "update": {
        ApiType.REST: OperationAssets(
            get_update_system_prompt,
            get_update_user_prompt,
            "rest/60-update.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SCIM: OperationAssets(
            get_scim_update_system_prompt,
            get_scim_update_user_prompt,
            "scim/60-update.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SQL: OperationAssets(
            get_sql_update_system_prompt,
            get_sql_update_user_prompt,
            "sql/update.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
    },
    "delete": {
        ApiType.REST: OperationAssets(
            get_delete_system_prompt,
            get_delete_user_prompt,
            "rest/70-delete.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SCIM: OperationAssets(
            get_scim_delete_system_prompt,
            get_scim_delete_user_prompt,
            "scim/70-delete.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SQL: OperationAssets(
            get_sql_delete_system_prompt,
            get_sql_delete_user_prompt,
            "sql/delete.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
    },
    "native_schema": {
        ApiType.REST: OperationAssets(
            get_native_schema_system_prompt,
            get_native_schema_user_prompt,
            "rest/25-user-schema.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
            connid_docs_path=CONNID_ATTRIBUTES_DOCS_PATH,
        ),
        ApiType.SCIM: OperationAssets(
            get_scim_native_schema_system_prompt,
            get_scim_native_schema_user_prompt,
            "scim/25-schema-customization.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
            connid_docs_path=CONNID_ATTRIBUTES_DOCS_PATH,
        ),
        ApiType.SQL: OperationAssets(
            get_sql_native_schema_system_prompt,
            get_sql_native_schema_user_prompt,
            "sql/schema-customization.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
            connid_docs_path=CONNID_ATTRIBUTES_DOCS_PATH,
        ),
    },
    # TODO add new documentation for authorization
    "authorization": {
        ApiType.REST: OperationAssets(
            get_authorization_system_prompt,
            get_authorization_user_prompt,
            "rest/xx-authorization.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        ApiType.SCIM: OperationAssets(
            get_authorization_system_prompt,
            get_authorization_user_prompt,
            "scim/xx-authorization.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
    },
}

SEARCH_PROMPT_MAP: Mapping[ApiType, Mapping[SearchIntent, OperationAssets]] = {
    ApiType.REST: {
        SearchIntent.ALL: OperationAssets(
            get_search_all_system_prompt,
            get_search_user_prompt,
            "rest/40-search-users.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.FILTER: OperationAssets(
            get_search_filter_system_prompt,
            get_search_user_prompt,
            "rest/40-search-users.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.ID: OperationAssets(
            get_search_id_system_prompt,
            get_search_user_prompt,
            "rest/40-search-users.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
    },
    ApiType.SCIM: {
        SearchIntent.ALL: OperationAssets(
            get_scim_search_all_system_prompt,
            get_scim_search_user_prompt,
            "scim/40-search.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.FILTER: OperationAssets(
            get_scim_search_filter_system_prompt,
            get_scim_search_user_prompt,
            "scim/40-search.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.ID: OperationAssets(
            get_scim_search_id_system_prompt,
            get_scim_search_user_prompt,
            "scim/40-search.adoc",
            declarative_docs_path=SCIM_REST_DECLARATIVE_DOCS_PATH,
        ),
    },
    ApiType.SQL: {
        SearchIntent.ALL: OperationAssets(
            get_sql_search_all_system_prompt,
            get_sql_search_user_prompt,
            "sql/search.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.FILTER: OperationAssets(
            get_sql_search_filter_system_prompt,
            get_sql_search_user_prompt,
            "sql/search.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
        SearchIntent.ID: OperationAssets(
            get_sql_search_id_system_prompt,
            get_sql_search_user_prompt,
            "sql/search.adoc",
            declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH,
        ),
    },
}


def get_operation_assets(operation: str, protocol: ApiType) -> OperationAssets:
    op = operation.lower()
    if op not in PROMPT_MAP or protocol not in PROMPT_MAP[op]:
        raise ValueError(f"Unsupported operation/protocol: {operation}/{protocol}")
    return PROMPT_MAP[op][protocol]


def get_search_operation_assets(protocol: ApiType, intent: SearchIntent | str) -> OperationAssets:
    normalized_intent = SearchIntent(intent) if isinstance(intent, str) else intent
    if protocol not in SEARCH_PROMPT_MAP or normalized_intent not in SEARCH_PROMPT_MAP[protocol]:
        raise ValueError(f"Unsupported search intent/protocol: {normalized_intent}/{protocol}")
    return SEARCH_PROMPT_MAP[protocol][normalized_intent]


def resolve_operation_docs_paths(
    kind: ArtifactKind,
    protocol: ApiType,
    *,
    intent: SearchIntent | None = None,
) -> tuple[str, ...]:
    """
    Resolve the bundled DSL references for one generated artifact, primary first.

    A native-schema artifact resolves to three documents - the protocol's operation DSL,
    the declarative-YAML reference, then the ConnID mapping reference - because its script
    carries both the attribute definitions and the ConnID mapping. The order is part of the
    contract: the fix prompt tells the model that the first document is the syntax authority
    and the declarative reference is what makes YAML a valid alternative to it.

    Returns an empty tuple when the combination has no bundled reference rather
    than raising, so an object-class caller can carry on with the documents it has.
    """
    if kind is ArtifactKind.CONNID:
        # No slot produces this kind any more, but a connector-fix job scheduled
        # before the ConnID mapping moved into the native schema still rehydrates
        # one from its persisted input, and it needs its reference.
        return (CONNID_ATTRIBUTES_DOCS_PATH,)

    try:
        if kind is ArtifactKind.SEARCH:
            if intent is None:
                raise ValueError("Search artifacts require an intent to resolve their documentation")
            assets = get_search_operation_assets(protocol, intent)
        else:
            operation_name = _ARTIFACT_OPERATION_NAMES.get(kind)
            if operation_name is None:
                return ()
            assets = get_operation_assets(operation_name, protocol)
    except ValueError:
        return ()

    if assets.connid_docs_path is None:
        return (assets.docs_path, assets.declarative_docs_path)
    return (assets.docs_path, assets.declarative_docs_path, assets.connid_docs_path)


_ARTIFACT_OPERATION_NAMES: Mapping[ArtifactKind, str] = {
    ArtifactKind.NATIVE_SCHEMA: "native_schema",
    ArtifactKind.CREATE: "create",
    ArtifactKind.UPDATE: "update",
    ArtifactKind.DELETE: "delete",
}
