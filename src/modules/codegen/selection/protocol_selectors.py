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
from src.modules.codegen.prompts.relation_prompts import get_relation_system_prompt, get_relation_user_prompt
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

# A single registry binds operation, protocol, prompts, expert reference and YAML sections.
CONNID_ATTRIBUTES_DOCS_PATH = "connid-attributes.adoc"
SCIM_REST_DECLARATIVE_DOCS_PATH = "declarative-yaml.adoc"
SQL_DECLARATIVE_DOCS_PATH = "sql/declarative-yaml.adoc"


def _assets(operation: str, protocol: ApiType, system: str, user: str) -> OperationAssets:
    folder = protocol.value.lower()
    sql = protocol is ApiType.SQL
    path = f"{folder}/{operation.replace('_', '-')}.adoc"
    additional: tuple[str, ...] = ()
    if operation == "authorization":
        path = "authentication.adoc"
    elif operation == "relationship":
        path = {
            ApiType.REST: "rest/relationship.adoc",
            ApiType.SCIM: "scim/relationship-support.adoc",
            ApiType.SQL: "sql/relationships.adoc",
        }[protocol]
        if protocol is ApiType.SCIM:
            additional = ("rest/relationship.adoc",)
    elif operation == "search":
        additional = {
            ApiType.SQL: ("sql/custom-search.adoc",),
            ApiType.REST: ("rest/search-reference.adoc", "rest/custom-search.adoc"),
            ApiType.SCIM: ("scim/advanced-filters.adoc", "rest/custom-search.adoc"),
        }[protocol]
    elif operation == "create" and protocol is ApiType.SCIM:
        additional = ("rest/create.adoc",)
    elif operation == "native_schema" and protocol is ApiType.SCIM:
        additional = ("scim/complex-attributes.adoc",)
    elif sql and operation in {"create", "update", "delete"}:
        additional = ("sql/related-table-writes.adoc",)

    if sql:
        sections = (
            ("Native YAML schema documents",)
            if operation in {"native_schema", "relationship"}
            else ("Operation documents",)
        )
    else:
        sections = {
            "native_schema": ("Schema documents",),
            "relationship": ("Schema documents",),
            "search": ("Search",),
            "authorization": ("Authentication",),
        }.get(operation, ("Create / update / delete",))
    return OperationAssets(
        system,
        user,
        path,
        declarative_docs_path=SQL_DECLARATIVE_DOCS_PATH if sql else SCIM_REST_DECLARATIVE_DOCS_PATH,
        connid_docs_path=CONNID_ATTRIBUTES_DOCS_PATH if operation == "native_schema" else None,
        additional_docs_paths=additional,
        docs_sections=("The same search in YAML",) if operation == "search" and protocol is ApiType.REST else (),
        declarative_sections=sections,
    )


PROMPT_MAP: Mapping[str, Mapping[ApiType, OperationAssets]] = {
    "native_schema": {
        ApiType.REST: _assets(
            "native_schema", ApiType.REST, get_native_schema_system_prompt, get_native_schema_user_prompt
        ),
        ApiType.SCIM: _assets(
            "native_schema", ApiType.SCIM, get_scim_native_schema_system_prompt, get_scim_native_schema_user_prompt
        ),
        ApiType.SQL: _assets(
            "native_schema", ApiType.SQL, get_sql_native_schema_system_prompt, get_sql_native_schema_user_prompt
        ),
    },
    "create": {
        ApiType.REST: _assets("create", ApiType.REST, get_create_system_prompt, get_create_user_prompt),
        ApiType.SCIM: _assets("create", ApiType.SCIM, get_scim_create_system_prompt, get_scim_create_user_prompt),
        ApiType.SQL: _assets("create", ApiType.SQL, get_sql_create_system_prompt, get_sql_create_user_prompt),
    },
    "update": {
        ApiType.REST: _assets("update", ApiType.REST, get_update_system_prompt, get_update_user_prompt),
        ApiType.SCIM: _assets("update", ApiType.SCIM, get_scim_update_system_prompt, get_scim_update_user_prompt),
        ApiType.SQL: _assets("update", ApiType.SQL, get_sql_update_system_prompt, get_sql_update_user_prompt),
    },
    "delete": {
        ApiType.REST: _assets("delete", ApiType.REST, get_delete_system_prompt, get_delete_user_prompt),
        ApiType.SCIM: _assets("delete", ApiType.SCIM, get_scim_delete_system_prompt, get_scim_delete_user_prompt),
        ApiType.SQL: _assets("delete", ApiType.SQL, get_sql_delete_system_prompt, get_sql_delete_user_prompt),
    },
    "authorization": {
        ApiType.REST: _assets(
            "authorization", ApiType.REST, get_authorization_system_prompt, get_authorization_user_prompt
        ),
        ApiType.SCIM: _assets(
            "authorization", ApiType.SCIM, get_authorization_system_prompt, get_authorization_user_prompt
        ),
    },
    "relationship": {
        ApiType.REST: _assets("relationship", ApiType.REST, get_relation_system_prompt, get_relation_user_prompt),
        ApiType.SCIM: _assets("relationship", ApiType.SCIM, get_relation_system_prompt, get_relation_user_prompt),
        ApiType.SQL: _assets("relationship", ApiType.SQL, get_relation_system_prompt, get_relation_user_prompt),
    },
}

SEARCH_PROMPT_MAP: Mapping[ApiType, Mapping[SearchIntent, OperationAssets]] = {
    ApiType.REST: {
        SearchIntent.ALL: _assets("search", ApiType.REST, get_search_all_system_prompt, get_search_user_prompt),
        SearchIntent.FILTER: _assets("search", ApiType.REST, get_search_filter_system_prompt, get_search_user_prompt),
        SearchIntent.ID: _assets("search", ApiType.REST, get_search_id_system_prompt, get_search_user_prompt),
    },
    ApiType.SCIM: {
        SearchIntent.ALL: _assets(
            "search", ApiType.SCIM, get_scim_search_all_system_prompt, get_scim_search_user_prompt
        ),
        SearchIntent.FILTER: _assets(
            "search", ApiType.SCIM, get_scim_search_filter_system_prompt, get_scim_search_user_prompt
        ),
        SearchIntent.ID: _assets("search", ApiType.SCIM, get_scim_search_id_system_prompt, get_scim_search_user_prompt),
    },
    ApiType.SQL: {
        SearchIntent.ALL: _assets("search", ApiType.SQL, get_sql_search_all_system_prompt, get_sql_search_user_prompt),
        SearchIntent.FILTER: _assets(
            "search", ApiType.SQL, get_sql_search_filter_system_prompt, get_sql_search_user_prompt
        ),
        SearchIntent.ID: _assets("search", ApiType.SQL, get_sql_search_id_system_prompt, get_sql_search_user_prompt),
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
        return (assets.docs_path, assets.declarative_docs_path, *assets.additional_docs_paths)
    return (assets.docs_path, assets.declarative_docs_path, assets.connid_docs_path, *assets.additional_docs_paths)


_ARTIFACT_OPERATION_NAMES: Mapping[ArtifactKind, str] = {
    ArtifactKind.AUTHORIZATION: "authorization",
    ArtifactKind.RELATION: "relationship",
    ArtifactKind.NATIVE_SCHEMA: "native_schema",
    ArtifactKind.CREATE: "create",
    ArtifactKind.UPDATE: "update",
    ArtifactKind.DELETE: "delete",
}
