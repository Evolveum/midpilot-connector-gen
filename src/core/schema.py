# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic.alias_generators import to_camel


class CamelCaseModel(BaseModel):
    """
    Base for models that expose camelCase field aliases.

    Accepts both snake_case field names and camelCase aliases on input and
    serializes to camelCase when dumped with ``by_alias=True``. Explicit
    per-field aliases keep precedence over the generated ones.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


def validate_pydantic_object(obj: Any, model: Any) -> Any:
    """
    Check if an object is a valid Pydantic model instance.
    This method is used in two scenarios:
    1. To validate if a single object conforms to a Pydantic model.
    2. To convert and validate any object to a Pydantic model instance.
    inputs:
        obj - object to validate
        model - Pydantic model class
    output:
        Adapted object if valid, else False
    """

    # This is a really ugly design - using try except to validate URL but pydantic does not seem to have a simple validate function
    try:
        validator = TypeAdapter(model)
        return validator.validate_python(obj)
    except Exception:
        return False
