# Copyright (C) 2010-2026 Evolveum and contributors
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import yaml

from src.modules.codegen.core.base import BaseGroovyGenerator
from src.modules.codegen.core.operations import (
    CreateGenerator,
    DeleteGenerator,
    RelationGenerator,
    SearchGenerator,
    UpdateGenerator,
)
from src.modules.codegen.enums import ConnectorCodeFormat, SearchIntent
from src.modules.codegen.repair import NO_DOCUMENTED_BEHAVIOR
from src.modules.codegen.utils.connector_code_validation import detect_connector_code_format, validate_connector_code
from src.modules.codegen.utils.operation_defaults import (
    DEFAULTS_COMMENT,
    authorization_defaults_artifact,
    is_empty_operation_artifact,
    normalize_operation_defaults,
    operation_defaults_artifact,
    relation_defaults_artifact,
    rest_operation_placeholder_artifact,
    rest_relation_placeholder_artifact,
)
from src.shared.enums import ApiType


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


async def _generate(generator: BaseGroovyGenerator, response: str):
    chain = AsyncMock()
    chain.ainvoke.return_value = response
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
    chain.ainvoke.assert_awaited_once()
    return result, errors, cleanup_chain


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["SQL", "SCIM"])
@pytest.mark.parametrize("generator_class", [SearchGenerator, CreateGenerator, UpdateGenerator, DeleteGenerator])
async def test_generation_and_cleanup_normalize_native_defaults(protocol, generator_class):
    result, errors, cleanup_chain = await _generate(make_generator(generator_class, protocol), "```yaml\ncode:{}\n```")
    errors.assert_not_awaited()
    assert DEFAULTS_COMMENT in result
    assert validate_connector_code(result) is None
    cleanup_chain.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["SQL", "SCIM", "REST"])
@pytest.mark.parametrize("response", ["", "```yaml\n```", "{}", "objectClasses: {User: {}}"])
@pytest.mark.parametrize("generator_class", [SearchGenerator, CreateGenerator, UpdateGenerator, DeleteGenerator])
async def test_model_finding_nothing_returns_the_operation_fallback(protocol, response, generator_class):
    generator = make_generator(generator_class, protocol)
    operation = generator.config.operation_name.lower()
    result, errors, cleanup_chain = await _generate(generator, response)

    assert validate_connector_code(result) is None
    assert detect_connector_code_format(result) is ConnectorCodeFormat.YAML
    assert yaml.safe_load(result) == {"objectClasses": {"User": {operation: {}}}}
    if protocol == "REST":
        # REST has no framework defaults: say so and keep the missing behavior visible.
        assert result == rest_operation_placeholder_artifact("User", operation)
        assert DEFAULTS_COMMENT not in result
        errors.assert_awaited_once()
        assert errors.await_args.args[1] == NO_DOCUMENTED_BEHAVIOR
    else:
        assert result == operation_defaults_artifact("User", operation)
        errors.assert_not_awaited()
    # Explicit native defaults are an accepted candidate and still pass the cleanup pass.
    native_candidate = protocol != "REST" and response.startswith(("{", "objectClasses"))
    assert cleanup_chain.ainvoke.await_count == (1 if native_candidate else 0)


def test_user_facing_defaults_artifact_shape():
    assert operation_defaults_artifact("user", "search") == (
        '# No changes needed. Use framework defaults.\nobjectClasses:\n  "user":\n    search: {}'
    )


@pytest.mark.parametrize(
    "code",
    [
        operation_defaults_artifact("User", "search"),
        rest_operation_placeholder_artifact("User", "delete"),
        rest_operation_placeholder_artifact('odd "name"\n# injected', "create"),
        relation_defaults_artifact(),
        rest_relation_placeholder_artifact("membership\nnext line"),
        authorization_defaults_artifact("scim", ["note one", "note two"]),
    ],
)
def test_fallback_artifacts_are_valid_yaml_with_single_line_comments(code):
    assert validate_connector_code(code) is None
    assert detect_connector_code_format(code) is ConnectorCodeFormat.YAML
    assert all(line.startswith("#") for line in code.splitlines() if "TODO" in line or DEFAULTS_COMMENT in line)


def test_placeholder_is_never_rewritten_into_defaults():
    placeholder = rest_operation_placeholder_artifact("User", "create")
    assert normalize_operation_defaults(placeholder, object_class="User", operation="create") == placeholder
    assert not is_empty_operation_artifact(placeholder, object_class="User", operation="create")


def _relation_generator(protocol: ApiType) -> RelationGenerator:
    return RelationGenerator(
        relation_name="membership",
        protocol=protocol,
        docs_text="",
        declarative_docs_text="",
        system_prompt="system",
        user_prompt="user",
    )


@pytest.mark.parametrize("protocol", [ApiType.SQL, ApiType.SCIM, ApiType.REST])
def test_relation_fallback_and_empty_candidate(protocol):
    generator = _relation_generator(protocol)
    fallback = generator.fallback_artifact()
    if protocol == ApiType.REST:
        assert fallback.code == rest_relation_placeholder_artifact("membership")
        assert 'relationship("membership")' in fallback.code
        assert fallback.job_error == NO_DOCUMENTED_BEHAVIOR
        assert generator.normalize_generated_code("{}") == ""
    else:
        assert fallback.code == relation_defaults_artifact()
        assert fallback.job_error is None
        assert generator.normalize_generated_code("{}") == relation_defaults_artifact()
        assert generator.normalize_generated_code(relation_defaults_artifact()) == relation_defaults_artifact()


@pytest.mark.parametrize("code", ["# TODO: override is unsupported\n{}", 'relationship("membership") {}'])
def test_relation_candidate_with_explanation_or_behavior_is_kept(code):
    assert _relation_generator(ApiType.SQL).normalize_generated_code(code) == code
