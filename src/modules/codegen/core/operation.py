#  Copyright (C) 2010-2026 Evolveum and contributors
#
#  Licensed under the EUPL-1.2 or later.

"""
Concrete generator implementations for different Groovy operations.

This module contains specialized generators for search, create, update, delete, and relation
operations. Each generator extends BaseGroovyGenerator and implements operation-specific logic.

Protocol-specific prompts and documentation are selected automatically based on api_type.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ...digester.schema import RelationsResponse
from ..prompts.relationPrompts import get_relation_system_prompt, get_relation_user_prompt
from ..utils.protocol_selectors import select_prompts_for_protocol
from .base import (
    AttributesPayload,
    BaseGroovyGenerator,
    EndpointsPayload,
    OperationConfig,
    attributes_to_records,
    endpoints_to_records,
)

logger = logging.getLogger(__name__)


class SearchGenerator(BaseGroovyGenerator):
    """Generator for Groovy search {} blocks with protocol-aware prompts."""

    def __init__(
        self,
        object_class: str,
        api_types: List[str],
        docs_text: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize SearchGenerator with protocol-specific prompts.

        Args:
            object_class: Object class name
            api_types: List of API types from session metadata
            docs_text: Documentation text content
            extra_prompt_vars: Additional prompt variables
        """
        system_prompt, user_prompt = select_prompts_for_protocol("search", api_types)
        protocol = "SCIM" if "SCIM" in api_types else "REST"

        config = OperationConfig(
            operation_name="Search",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="search {\n}\n",
            logger_prefix=f"[Codegen:Search:{protocol}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["search_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        """Prepare attributes and endpoints as JSON strings."""
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]

        attrs_json = json.dumps(attributes_to_records(attributes), ensure_ascii=False)
        endpoints_json = json.dumps(endpoints_to_records(endpoints), ensure_ascii=False)

        return {"attributes_json": attrs_json, "endpoints_json": endpoints_json}

    def get_initial_result(self, **kwargs: Any) -> str:
        """Return initial scaffold for search operation."""
        return f'objectClass("{self.object_class}") {{\n    search {{\n    }}\n}}\n'


class CreateGenerator(BaseGroovyGenerator):
    """Generator for Groovy create {} blocks with protocol-aware prompts."""

    def __init__(
        self,
        object_class: str,
        api_types: List[str],
        docs_text: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize CreateGenerator with protocol-specific prompts.

        Args:
            object_class: Object class name
            api_types: List of API types from session metadata
            docs_text: Documentation text content
            extra_prompt_vars: Additional prompt variables
        """
        system_prompt, user_prompt = select_prompts_for_protocol("create", api_types)
        protocol = "SCIM" if "SCIM" in api_types else "REST"

        config = OperationConfig(
            operation_name="Create",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="create {\n}\n",
            logger_prefix=f"[Codegen:Create:{protocol}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["create_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        """Prepare attributes and endpoints as JSON strings."""
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]

        attrs_json = json.dumps(attributes_to_records(attributes), ensure_ascii=False)
        endpoints_json = json.dumps(endpoints_to_records(endpoints), ensure_ascii=False)

        return {"attributes_json": attrs_json, "endpoints_json": endpoints_json}

    def get_initial_result(self, **kwargs: Any) -> str:
        """Return initial scaffold for create operation."""
        return f'objectClass("{self.object_class}") {{\n    create {{\n    }}\n}}\n'


class UpdateGenerator(BaseGroovyGenerator):
    """Generator for Groovy update {} blocks with protocol-aware prompts."""

    def __init__(
        self,
        object_class: str,
        api_types: List[str],
        docs_text: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize UpdateGenerator with protocol-specific prompts.

        Args:
            object_class: Object class name
            api_types: List of API types from session metadata
            docs_text: Documentation text content
            extra_prompt_vars: Additional prompt variables
        """
        system_prompt, user_prompt = select_prompts_for_protocol("update", api_types)
        protocol = "SCIM" if "SCIM" in api_types else "REST"

        config = OperationConfig(
            operation_name="Update",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="update {\n}\n",
            logger_prefix=f"[Codegen:Update:{protocol}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["update_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        """Prepare attributes and endpoints as JSON strings."""
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]

        attrs_json = json.dumps(attributes_to_records(attributes), ensure_ascii=False)
        endpoints_json = json.dumps(endpoints_to_records(endpoints), ensure_ascii=False)

        return {"attributes_json": attrs_json, "endpoints_json": endpoints_json}

    def get_initial_result(self, **kwargs: Any) -> str:
        """Return initial scaffold for update operation."""
        return f'objectClass("{self.object_class}") {{\n    update {{\n    }}\n}}\n'


class DeleteGenerator(BaseGroovyGenerator):
    """Generator for Groovy delete {} blocks with protocol-aware prompts."""

    def __init__(
        self,
        object_class: str,
        api_types: List[str],
        docs_text: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize DeleteGenerator with protocol-specific prompts.

        Args:
            object_class: Object class name
            api_types: List of API types from session metadata
            docs_text: Documentation text content
            extra_prompt_vars: Additional prompt variables
        """
        system_prompt, user_prompt = select_prompts_for_protocol("delete", api_types)
        protocol = "SCIM" if "SCIM" in api_types else "REST"

        config = OperationConfig(
            operation_name="Delete",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="delete {\n}\n",
            logger_prefix=f"[Codegen:Delete:{protocol}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["delete_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        """Prepare attributes and endpoints as JSON strings."""
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]

        attrs_json = json.dumps(attributes_to_records(attributes), ensure_ascii=False)
        endpoints_json = json.dumps(endpoints_to_records(endpoints), ensure_ascii=False)

        return {"attributes_json": attrs_json, "endpoints_json": endpoints_json}

    def get_initial_result(self, **kwargs: Any) -> str:
        """Return initial scaffold for delete operation."""
        return f'objectClass("{self.object_class}") {{\n    delete {{\n    }}\n}}\n'


class RelationGenerator(BaseGroovyGenerator):
    """Generator for Groovy relation {} blocks."""

    def __init__(self, docs_text: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        """
        Initialize RelationGenerator.

        Args:
            docs_text: Documentation text content
            extra_prompt_vars: Additional prompt variables
        """
        config = OperationConfig(
            operation_name="Relation",
            system_prompt=get_relation_system_prompt,
            user_prompt=get_relation_user_prompt,
            default_scaffold="relation {\n}\n",
            logger_prefix="[Codegen:Relation]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["relation_docs"] = docs_text
        super().__init__(config)

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        """Prepare relations as JSON string."""

        relations = kwargs.get("relations")

        # Convert relations to JSON text
        if isinstance(relations, RelationsResponse):
            relation_json = relations.model_dump_json()
        elif isinstance(relations, str):
            relation_json = relations
        else:
            try:
                relation_json = json.dumps(relations, ensure_ascii=False)
            except Exception:
                relation_json = json.dumps({"relations": []})

        return {"relation_json": relation_json}

    def get_initial_result(self, **kwargs: Any) -> str:
        """Return empty initial result for relation operation."""
        return ""
