# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import yaml

from src.modules.codegen.core.base import BaseGroovyGenerator
from src.modules.codegen.core.operations import CreateGenerator, DeleteGenerator, SearchGenerator, UpdateGenerator
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.utils.connector_code_validation import validate_connector_code
from src.modules.codegen.utils.operation_defaults import DEFAULTS_COMMENT, normalize_operation_defaults


@pytest.mark.parametrize("operation", ["search", "create", "update", "delete"])
@pytest.mark.parametrize("code", ["{}", "code: {}", "code:{}", "objectClasses: {User: {}}"])
def test_empty_yaml_is_scoped_and_explained(operation, code):
    result = normalize_operation_defaults(code, object_class="User", operation=operation)
    assert DEFAULTS_COMMENT in result
    assert yaml.safe_load(result) == {"objectClasses": {"User": {operation: {}}}}
    assert validate_connector_code(result) is None
    assert normalize_operation_defaults(result, object_class="User", operation=operation) == result


@pytest.mark.parametrize("operation", ["search", "create", "update", "delete"])
@pytest.mark.parametrize("body", ["", "{operation} {{}}", "{operation} {{ scim {{}} }}", "{operation} {{ sql {{}} }}"])
def test_empty_groovy_preserves_format(operation, body):
    code = 'objectClass("User") { ' + body.format(operation=operation) + " }"
    result = normalize_operation_defaults(code, object_class="User", operation=operation)
    assert f"// {DEFAULTS_COMMENT}" in result
    assert f"{operation} {{" in result
    assert validate_connector_code(result) is None
    assert normalize_operation_defaults(result, object_class="User", operation=operation) == result


@pytest.mark.parametrize(
    "code",
    [
        "",
        "   ",
        "# TODO unsupported requirement\n{}",
        "# Missing evidence\n{}",
        "objectClasses: {Other: {}}",
        "objectClasses: {User: {create: {enabled: false}}}",
        "objectClasses: {User: {update: {}}}",
        "objectClasses: {User: {}, User: {}}",
        'objectClass("User") { create { enabled false } }',
        'objectClass("Other") {}',
        "code: {unexpected: true}",
        "invalid: [",
        '// TODO missing evidence\nobjectClass("User") {}',
    ],
)
def test_missing_invalid_or_custom_output_is_not_replaced(code):
    assert normalize_operation_defaults(code, object_class="User", operation="create") == code


def make_generator(
    generator_class: type[SearchGenerator | CreateGenerator | UpdateGenerator | DeleteGenerator],
    protocol: str,
) -> BaseGroovyGenerator:
    if issubclass(generator_class, SearchGenerator):
        return generator_class(
            object_class="User",
            intent=SearchIntent.ALL,
            docs_text="",
            declarative_docs_text="",
            system_prompt="system",
            user_prompt="user",
            protocol_label=protocol,
        )
    return generator_class(
        object_class="User",
        docs_text="",
        declarative_docs_text="",
        system_prompt="system",
        user_prompt="user",
        protocol_label=protocol,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["SQL", "SCIM", "REST"])
@pytest.mark.parametrize("generator_class", [SearchGenerator, CreateGenerator, UpdateGenerator, DeleteGenerator])
async def test_generation_and_cleanup_normalize_only_native_defaults(protocol, generator_class):
    generator = make_generator(generator_class, protocol)
    chain = AsyncMock()
    chain.ainvoke.return_value = "```yaml\ncode:{}\n```" if protocol != "REST" else "{}"
    cleanup_chain = AsyncMock()
    cleanup_chain.ainvoke.return_value = "{}"
    with (
        patch.object(generator, "_load_documentation_items", AsyncMock(return_value=[{"content": "docs"}])),
        patch.object(generator, "_initialize_progress", AsyncMock()),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain", return_value=cleanup_chain),
        patch("src.modules.codegen.core.base.increment_processed_documents", AsyncMock()),
        patch("src.modules.codegen.core.base.append_job_error", AsyncMock()) as errors,
    ):
        result = await generator.generate(session_id=uuid4(), job_id=uuid4(), attributes={})
    errors.assert_not_awaited()
    assert (DEFAULTS_COMMENT in result) is (protocol != "REST")
    assert validate_connector_code(result) is None
    chain.ainvoke.assert_awaited_once()
    cleanup_chain.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["SQL", "SCIM"])
async def test_empty_model_response_remains_a_generation_failure(protocol):
    generator = make_generator(CreateGenerator, protocol)
    chain = AsyncMock()
    chain.ainvoke.return_value = ""
    with (
        patch.object(generator, "_load_documentation_items", AsyncMock(return_value=[{"content": "docs"}])),
        patch.object(generator, "_initialize_progress", AsyncMock()),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch.object(generator, "_cleanup_generated_code", AsyncMock()) as cleanup,
        patch("src.modules.codegen.core.base.increment_processed_documents", AsyncMock()),
        patch("src.modules.codegen.core.base.append_job_error", AsyncMock()) as errors,
    ):
        result = await generator.generate(session_id=uuid4(), job_id=uuid4(), attributes={})
    assert result == ""
    errors.assert_awaited_once()
    cleanup.assert_not_awaited()
