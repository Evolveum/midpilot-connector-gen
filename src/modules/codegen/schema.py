# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.
from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.utils.groovy_validation import ensure_valid_groovy_code


@dataclass(frozen=True)
class OperationAssets:
    system_prompt: str
    user_prompt: str
    docs_path: str


_SEARCH_INTENT_SUFFIX: dict[SearchIntent, str] = {
    SearchIntent.ALL: "All",
    SearchIntent.FILTER: "Filter",
    SearchIntent.ID: "Id",
}


def build_search_operation_key(object_class: str, intent: SearchIntent | str) -> str:
    normalized_intent = SearchIntent(intent) if isinstance(intent, str) else intent
    return f"{object_class}Search{_SEARCH_INTENT_SUFFIX[normalized_intent]}"


class GroovyCodePayload(BaseModel):
    code: str = Field(..., description="Groovy code")

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return ensure_valid_groovy_code(value)
