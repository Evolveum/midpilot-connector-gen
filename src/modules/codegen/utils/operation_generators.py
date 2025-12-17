# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Concrete generator implementations for different Groovy operations.

This module contains specialized generators for search, create, update, delete, and relation
operations. Each generator extends BaseGroovyGenerator and implements operation-specific logic.
"""

import json
import logging
from typing import Any, Dict, Optional

from ...digester.schema import RelationsResponse
from ..prompts.createPrompts import get_create_system_prompt, get_create_user_prompt
from ..prompts.deletePrompts import get_delete_system_prompt, get_delete_user_prompt
from ..prompts.relationPrompts import get_relation_system_prompt, get_relation_user_prompt
from ..prompts.searchPrompts import get_search_system_prompt, get_search_user_prompt
from ..prompts.updatePrompts import get_update_system_prompt, get_update_user_prompt
from .base_generator import (
    AttributesPayload,
    BaseGroovyGenerator,
    EndpointsPayload,
    OperationConfig,
    attributes_to_records,
    endpoints_to_records,
)

logger = logging.getLogger(__name__)


class SearchGenerator(BaseGroovyGenerator):
    """Generator for Groovy search {} blocks."""

    def __init__(self, object_class: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        config = OperationConfig(
            operation_name="Search",
            system_prompt=get_search_system_prompt,
            user_prompt=get_search_user_prompt,
            default_scaffold="search {\n}\n",
            logger_prefix="[Codegen:Search]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
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
    """Generator for Groovy create {} blocks."""

    def __init__(self, object_class: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        config = OperationConfig(
            operation_name="Create",
            system_prompt=get_create_system_prompt,
            user_prompt=get_create_user_prompt,
            default_scaffold="create {\n}\n",
            logger_prefix="[Codegen:Create]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
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
    """Generator for Groovy update {} blocks."""

    def __init__(self, object_class: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        config = OperationConfig(
            operation_name="Update",
            system_prompt=get_update_system_prompt,
            user_prompt=get_update_user_prompt,
            default_scaffold="update {\n}\n",
            logger_prefix="[Codegen:Update]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
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
    """Generator for Groovy delete {} blocks."""

    def __init__(self, object_class: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        config = OperationConfig(
            operation_name="Delete",
            system_prompt=get_delete_system_prompt,
            user_prompt=get_delete_user_prompt,
            default_scaffold="delete {\n}\n",
            logger_prefix="[Codegen:Delete]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
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

    def __init__(self, extra_prompt_vars: Optional[Dict[str, Any]] = None):
        config = OperationConfig(
            operation_name="Relation",
            system_prompt=get_relation_system_prompt,
            user_prompt=get_relation_user_prompt,
            default_scaffold="relation {\n}\n",
            logger_prefix="[Codegen:Relation]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
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
