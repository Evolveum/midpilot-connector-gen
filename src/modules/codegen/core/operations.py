# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, Optional

from src.modules.codegen.core.base import (
    BaseGroovyGenerator,
    endpoints_to_records,
)
from src.modules.codegen.enums import SearchIntent, search_intent_label
from src.modules.codegen.repair import NO_DOCUMENTED_BEHAVIOR
from src.modules.codegen.schema import AttributesPayload, EndpointsPayload, FallbackArtifact, OperationConfig
from src.modules.codegen.selection.authorization import ANALYSIS_SUPPORT_FIELD, ANALYSIS_SUPPORT_UNSUPPORTED
from src.modules.codegen.utils.operation_defaults import (
    authorization_defaults_artifact,
    is_empty_authorization_artifact,
    is_empty_operation_artifact,
    is_empty_relation_artifact,
    normalize_operation_defaults,
    operation_defaults_artifact,
    relation_defaults_artifact,
    rest_operation_placeholder_artifact,
    rest_relation_placeholder_artifact,
)
from src.modules.codegen.utils.prompt_records import (
    build_attribute_context_records,
    build_scim_contract_prompt_vars,
)
from src.modules.digester.schemas import RelationsResponse
from src.shared.enums import ApiType

logger = logging.getLogger(__name__)

# REST operations and relationships exist only where an artifact declares them.
_PROTOCOLS_WITH_FRAMEWORK_DEFAULTS = frozenset({ApiType.SQL, ApiType.SCIM})


def _prepare_operation_input_data(
    attributes: AttributesPayload,
    endpoints: Optional[EndpointsPayload],
    *,
    include_scim_context: bool,
) -> Dict[str, str]:
    endpoint_records = endpoints_to_records(endpoints) if endpoints is not None else []
    input_data = {
        "attributes_json": json.dumps(build_attribute_context_records(attributes), ensure_ascii=False),
        "endpoints_json": json.dumps(endpoint_records, ensure_ascii=False),
    }
    if include_scim_context:
        input_data.update(build_scim_contract_prompt_vars(attributes, endpoints))
    return input_data


class _OperationGenerator(BaseGroovyGenerator):
    """Apply the framework-defaults contract to CRUD/search candidates, cleanup and empty runs."""

    def __init__(self, config: OperationConfig, protocol_label: str):
        super().__init__(config)
        self.protocol = ApiType(protocol_label.lower())
        self.object_class: str = config.extra_prompt_vars["object_class"]
        self.operation = config.operation_name.lower()

    def normalize_generated_code(self, code: str) -> str:
        if self.protocol in _PROTOCOLS_WITH_FRAMEWORK_DEFAULTS:
            return normalize_operation_defaults(code, object_class=self.object_class, operation=self.operation)
        # Without framework defaults an explicit empty artifact implements nothing.
        if is_empty_operation_artifact(code, object_class=self.object_class, operation=self.operation):
            return ""
        return code

    def fallback_artifact(self) -> FallbackArtifact:
        if self.protocol in _PROTOCOLS_WITH_FRAMEWORK_DEFAULTS:
            return FallbackArtifact(operation_defaults_artifact(self.object_class, self.operation))
        return FallbackArtifact(
            rest_operation_placeholder_artifact(self.object_class, self.operation),
            job_error=NO_DOCUMENTED_BEHAVIOR,
        )


class SearchGenerator(_OperationGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        intent: SearchIntent,
        preferred_endpoints: Optional[list[Dict[str, Any]]] = None,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        base_api_url: str = "",
        database_name: str = "",
        include_scim_context: bool = False,
        context_only_for_conndev: bool = False,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Search",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name=f"Codegen:Search:{protocol_label}:{search_intent_label(intent)}",
            extra_prompt_vars=extra_prompt_vars or {},
            context_only_for_conndev=context_only_for_conndev,
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["intent"] = intent
        config.extra_prompt_vars["search_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        config.extra_prompt_vars["base_api_url"] = base_api_url
        config.extra_prompt_vars["database_name"] = database_name
        config.extra_prompt_vars["preferred_endpoints_json"] = json.dumps(preferred_endpoints or [], ensure_ascii=False)
        super().__init__(config, protocol_label)
        self.include_scim_context = include_scim_context

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: Optional[EndpointsPayload] = kwargs.get("endpoints")  # type: ignore[assignment]
        return _prepare_operation_input_data(
            attributes,
            endpoints,
            include_scim_context=self.include_scim_context,
        )


class CreateGenerator(_OperationGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        preferred_endpoints: Optional[list[Dict[str, Any]]] = None,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        base_api_url: str = "",
        database_name: str = "",
        include_scim_context: bool = False,
        context_only_for_conndev: bool = False,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Create",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name=f"Codegen:Create:{protocol_label}",
            extra_prompt_vars=extra_prompt_vars or {},
            context_only_for_conndev=context_only_for_conndev,
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["create_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        config.extra_prompt_vars["base_api_url"] = base_api_url
        config.extra_prompt_vars["database_name"] = database_name
        config.extra_prompt_vars["preferred_endpoints_json"] = json.dumps(preferred_endpoints or [], ensure_ascii=False)
        super().__init__(config, protocol_label)
        self.include_scim_context = include_scim_context

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: Optional[EndpointsPayload] = kwargs.get("endpoints")  # type: ignore[assignment]
        return _prepare_operation_input_data(
            attributes,
            endpoints,
            include_scim_context=self.include_scim_context,
        )


class UpdateGenerator(_OperationGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        preferred_endpoints: Optional[list[Dict[str, Any]]] = None,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        base_api_url: str = "",
        database_name: str = "",
        include_scim_context: bool = False,
        context_only_for_conndev: bool = False,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Update",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name=f"Codegen:Update:{protocol_label}",
            extra_prompt_vars=extra_prompt_vars or {},
            context_only_for_conndev=context_only_for_conndev,
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["update_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        config.extra_prompt_vars["base_api_url"] = base_api_url
        config.extra_prompt_vars["database_name"] = database_name
        config.extra_prompt_vars["preferred_endpoints_json"] = json.dumps(preferred_endpoints or [], ensure_ascii=False)
        super().__init__(config, protocol_label)
        self.include_scim_context = include_scim_context

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: Optional[EndpointsPayload] = kwargs.get("endpoints")  # type: ignore[assignment]
        return _prepare_operation_input_data(
            attributes,
            endpoints,
            include_scim_context=self.include_scim_context,
        )


class DeleteGenerator(_OperationGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        preferred_endpoints: Optional[list[Dict[str, Any]]] = None,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        base_api_url: str = "",
        database_name: str = "",
        include_scim_context: bool = False,
        context_only_for_conndev: bool = False,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Delete",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name=f"Codegen:Delete:{protocol_label}",
            extra_prompt_vars=extra_prompt_vars or {},
            context_only_for_conndev=context_only_for_conndev,
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["delete_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        config.extra_prompt_vars["base_api_url"] = base_api_url
        config.extra_prompt_vars["database_name"] = database_name
        config.extra_prompt_vars["preferred_endpoints_json"] = json.dumps(preferred_endpoints or [], ensure_ascii=False)
        super().__init__(config, protocol_label)
        self.include_scim_context = include_scim_context

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: Optional[EndpointsPayload] = kwargs.get("endpoints")  # type: ignore[assignment]
        return _prepare_operation_input_data(
            attributes,
            endpoints,
            include_scim_context=self.include_scim_context,
        )


class RelationGenerator(BaseGroovyGenerator):
    def __init__(
        self,
        *,
        relation_name: str,
        protocol: ApiType,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
    ):
        config = OperationConfig(
            operation_name="Relation",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name="Codegen:Relation",
        )
        config.extra_prompt_vars["protocol"] = protocol.value
        config.extra_prompt_vars["relation_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        super().__init__(config)
        self.relation_name = relation_name
        self.protocol = protocol

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        relations = kwargs.get("relations")
        if isinstance(relations, RelationsResponse):
            relation_json = relations.model_dump_json(by_alias=True)
        elif isinstance(relations, str):
            relation_json = relations
        else:
            try:
                relation_json = json.dumps(relations, ensure_ascii=False)
            except Exception:
                relation_json = json.dumps({"relations": []})
        return {"relation_json": relation_json, "relation_name": self.relation_name}

    def normalize_generated_code(self, code: str) -> str:
        if not is_empty_relation_artifact(code):
            return code
        # SQL discovery and SCIM automatic mappings cover a relationship needing no override.
        return relation_defaults_artifact() if self.protocol in _PROTOCOLS_WITH_FRAMEWORK_DEFAULTS else ""

    def fallback_artifact(self) -> FallbackArtifact:
        if self.protocol in _PROTOCOLS_WITH_FRAMEWORK_DEFAULTS:
            return FallbackArtifact(relation_defaults_artifact())
        return FallbackArtifact(
            rest_relation_placeholder_artifact(self.relation_name), job_error=NO_DOCUMENTED_BEHAVIOR
        )


def build_other_authorization_scaffold(protocol: ApiType) -> str:
    return (
        "authorization {\n"
        f"    {protocol.value} {{\n"
        "        other {\n"
        "            implementation {\n"
        "                // write your custom implementation of authorization here\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    )


def _comment_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _unsupported_authorization_comments(preferred_authorizations: Optional[list[Dict[str, Any]]]) -> list[str]:
    if not preferred_authorizations:
        return []

    comments: list[str] = []
    for authorization in preferred_authorizations:
        if authorization.get(ANALYSIS_SUPPORT_FIELD) != ANALYSIS_SUPPORT_UNSUPPORTED:
            continue

        name = _comment_text(authorization.get("name")) or "Selected authorization"
        auth_type = _comment_text(authorization.get("type"))
        label = f"{name} ({auth_type})" if auth_type else name
        comments.extend(
            [
                f"{label} was selected in midPoint, but it was not identified in the analyzed application documentation.",
                "No application-specific authorization customization can be generated for this method.",
            ]
        )

    return comments


class AuthorizationGenerator(BaseGroovyGenerator):
    def __init__(
        self,
        *,
        preferred_authorizations: Optional[list[Dict[str, Any]]] = None,
        docs_text: str,
        declarative_docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol: ApiType,
        base_api_url: str = "",
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        authentication_container = protocol.value
        config = OperationConfig(
            operation_name="Authorization",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_name=f"Codegen:Authorization:{protocol.value.upper()}",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["authorization_docs"] = docs_text
        config.extra_prompt_vars["declarative_docs"] = declarative_docs_text
        config.extra_prompt_vars["authentication_container"] = authentication_container
        config.extra_prompt_vars["base_api_url"] = base_api_url
        config.extra_prompt_vars["preferred_authorizations_json"] = json.dumps(
            preferred_authorizations or [], ensure_ascii=False
        )
        super().__init__(config)
        self.authentication_container = authentication_container
        self.protocol = protocol
        self.preferred_authorizations = preferred_authorizations

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        return {}

    def normalize_generated_code(self, code: str) -> str:
        if is_empty_authorization_artifact(code, namespace=self.authentication_container):
            return self.fallback_artifact().code
        return code

    def fallback_artifact(self) -> FallbackArtifact:
        # The selected methods are built into the framework; a script only refines them.
        return FallbackArtifact(
            authorization_defaults_artifact(
                self.authentication_container,
                _unsupported_authorization_comments(self.preferred_authorizations),
            )
        )
