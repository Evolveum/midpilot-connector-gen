# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import json
import logging
from typing import Any, Dict, Optional

from ...digester.schema import RelationsResponse
from ..prompts.relationPrompts import get_relation_system_prompt, get_relation_user_prompt
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
    def __init__(
        self,
        *,
        object_class: str,
        docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Search",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="search {\n}\n",
            logger_prefix=f"[Codegen:Search:{protocol_label}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["search_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]
        return {
            "attributes_json": json.dumps(attributes_to_records(attributes), ensure_ascii=False),
            "endpoints_json": json.dumps(endpoints_to_records(endpoints), ensure_ascii=False),
        }

    def get_initial_result(self, **kwargs: Any) -> str:
        return f'objectClass("{self.object_class}") {{\n    search {{\n    }}\n}}\n'


class CreateGenerator(BaseGroovyGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Create",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="create {\n}\n",
            logger_prefix=f"[Codegen:Create:{protocol_label}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["create_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]
        return {
            "attributes_json": json.dumps(attributes_to_records(attributes), ensure_ascii=False),
            "endpoints_json": json.dumps(endpoints_to_records(endpoints), ensure_ascii=False),
        }

    def get_initial_result(self, **kwargs: Any) -> str:
        return f'objectClass("{self.object_class}") {{\n    create {{\n    }}\n}}\n'


class UpdateGenerator(BaseGroovyGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Update",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="update {\n}\n",
            logger_prefix=f"[Codegen:Update:{protocol_label}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["update_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]
        return {
            "attributes_json": json.dumps(attributes_to_records(attributes), ensure_ascii=False),
            "endpoints_json": json.dumps(endpoints_to_records(endpoints), ensure_ascii=False),
        }

    def get_initial_result(self, **kwargs: Any) -> str:
        return f'objectClass("{self.object_class}") {{\n    update {{\n    }}\n}}\n'


class DeleteGenerator(BaseGroovyGenerator):
    def __init__(
        self,
        *,
        object_class: str,
        docs_text: str,
        system_prompt: str,
        user_prompt: str,
        protocol_label: str,
        extra_prompt_vars: Optional[Dict[str, Any]] = None,
    ):
        config = OperationConfig(
            operation_name="Delete",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            default_scaffold="delete {\n}\n",
            logger_prefix=f"[Codegen:Delete:{protocol_label}]",
            extra_prompt_vars=extra_prompt_vars or {},
        )
        config.extra_prompt_vars["object_class"] = object_class
        config.extra_prompt_vars["delete_docs"] = docs_text
        super().__init__(config)
        self.object_class = object_class

    def prepare_input_data(self, **kwargs: Any) -> Dict[str, str]:
        attributes: AttributesPayload = kwargs.get("attributes")  # type: ignore[assignment]
        endpoints: EndpointsPayload = kwargs.get("endpoints")  # type: ignore[assignment]
        return {
            "attributes_json": json.dumps(attributes_to_records(attributes), ensure_ascii=False),
            "endpoints_json": json.dumps(endpoints_to_records(endpoints), ensure_ascii=False),
        }

    def get_initial_result(self, **kwargs: Any) -> str:
        return f'objectClass("{self.object_class}") {{\n    delete {{\n    }}\n}}\n'


class RelationGenerator(BaseGroovyGenerator):
    def __init__(self, docs_text: str, extra_prompt_vars: Optional[Dict[str, Any]] = None):
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
        relations = kwargs.get("relations")
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
        return ""
