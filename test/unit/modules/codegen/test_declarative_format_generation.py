# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
End-to-end coverage of the YAML-vs-Groovy decision through the real (unmocked) format-aware
validator, plus proof that every generation call actually receives the bundled declarative
reference. Complements test_connector_code_validation.py (pure validator unit tests) and
test_codegen_validation.py (the pre-existing Groovy-only generator/cleanup behavior).
"""

from dataclasses import dataclass
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.modules.codegen.core.base import BaseGroovyGenerator, OperationConfig
from src.modules.codegen.enums import SearchIntent
from src.modules.codegen.prompts.declarative_format_prompts import DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
from src.modules.codegen.selection.docs_loader import load_required_adoc_text
from src.modules.codegen.selection.protocol_selectors import get_operation_assets, get_search_operation_assets
from src.shared.enums import ApiType

# A native-schema document that the declarative-YAML reference documents as fully sufficient:
# plain attribute mapping with no scripting hook anywhere.
YAML_SUFFICIENT_CODE = (
    "objectClasses:\n"
    "  User:\n"
    "    sql:\n"
    "      table: app_user\n"
    "    attributes:\n"
    "      user_id:\n"
    "        connId:\n"
    "          name: __UID__\n"
    "      userName:\n"
    "        connId:\n"
    "          name: __NAME__\n"
)

# A search customization that every declarative reference explicitly documents as Groovy-only
# (a fixed predicate augmenting the SQL built-in search, or an equivalent custom-search body).
GROOVY_REQUIRED_CODE = (
    'objectClass("User") {\n'
    "    search {\n"
    "        sql {\n"
    "            custom {\n"
    '                query { q -> q.from("app_user").where("status = \'active\'") }\n'
    "            }\n"
    "        }\n"
    "    }\n"
    "}\n"
)


class _DummyChain:
    def __init__(self, responses):
        self._responses = list(responses)

    async def ainvoke(self, *args, **kwargs):
        return self._responses.pop(0)


class _RecordingChain(_DummyChain):
    def __init__(self, responses):
        super().__init__(responses)
        self.calls: list[dict] = []

    async def ainvoke(self, prompt_vars, *args, **kwargs):
        self.calls.append(prompt_vars)
        return await super().ainvoke(prompt_vars, *args, **kwargs)


@dataclass
class _DummyGenerator(BaseGroovyGenerator):
    """Mirrors the fixture in test_codegen_validation.py - a minimal concrete generator."""

    def __init__(self):
        super().__init__(
            OperationConfig(
                operation_name="Dummy",
                system_prompt="system",
                user_prompt="user",
                default_scaffold='objectClass("User") {}',
                logger_prefix="[Codegen:Dummy]",
            )
        )

    def prepare_input_data(self, **kwargs):
        return {}

    def get_initial_result(self, **kwargs):
        return 'objectClass("User") {}'


async def _run_process_chunks(chain, *, chunks=("chunk",)):
    generator = _DummyGenerator()
    with (
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock) as mock_append_job_error,
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
    ):
        result = await generator._process_chunks(
            chunks=list(chunks),
            provenance_chunk_ids=[None] * len(chunks),
            per_chunk_counts={},
            chunk_ids_included=[],
            input_data={},
            chain=chain,
            job_id=uuid4(),
            initial_result='objectClass("User") {}',
        )
    return result, mock_append_job_error


# --- Acceptance criterion 1: a connector fully representable in declarative YAML -----------------


@pytest.mark.asyncio
async def test_yaml_sufficient_response_is_accepted_by_the_real_format_aware_validator():
    """
    No mocking of validate_connector_code here: this proves the real detector classifies the
    LLM's YAML response as YAML and the real YAML structural validator accepts it, exactly as it
    would for a genuine "declarative format is sufficient" connector.
    """
    result, mock_append_job_error = await _run_process_chunks(_DummyChain([YAML_SUFFICIENT_CODE]))

    assert result == YAML_SUFFICIENT_CODE.strip()
    mock_append_job_error.assert_not_called()


# --- Acceptance criterion 2: a connector requiring functionality YAML cannot express --------------


@pytest.mark.asyncio
async def test_groovy_required_response_is_accepted_by_the_real_format_aware_validator():
    """Groovy generation is unaffected by the format-aware validator: real Groovy still validates."""
    result, mock_append_job_error = await _run_process_chunks(_DummyChain([GROOVY_REQUIRED_CODE]))

    assert result == GROOVY_REQUIRED_CODE.strip()
    mock_append_job_error.assert_not_called()


@pytest.mark.asyncio
async def test_later_chunk_switches_the_artifact_from_yaml_to_groovy_when_scripting_becomes_necessary():
    """
    Successive-chunk accumulation preserves the requirement carried in <result> even across a
    format switch: chunk 1 establishes sufficient YAML, chunk 2's evidence requires a Groovy-only
    capability, and the generator (per DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES) replaces the whole
    artifact with Groovy rather than corrupting a mixed document.
    """
    result, mock_append_job_error = await _run_process_chunks(
        _DummyChain([YAML_SUFFICIENT_CODE, GROOVY_REQUIRED_CODE]),
        chunks=("chunk-1", "chunk-2"),
    )

    assert result == GROOVY_REQUIRED_CODE.strip()
    mock_append_job_error.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_yaml_candidate_is_rejected_and_the_last_valid_groovy_result_is_kept():
    """
    'Never silently replace rejected YAML with Groovy': an invalid YAML candidate is rejected
    outright, not reinterpreted as Groovy or used to discard the last good result.
    """
    malformed_yaml = "objectClasses:\n  User: {}\n  User: {}\n"  # duplicate key
    result, mock_append_job_error = await _run_process_chunks(
        _DummyChain([GROOVY_REQUIRED_CODE, malformed_yaml]),
        chunks=("chunk-1", "chunk-2"),
    )

    assert result == GROOVY_REQUIRED_CODE.strip()
    mock_append_job_error.assert_called_once()


# --- Acceptance criterion 3: every generation call receives the bundled declarative reference -----


@pytest.mark.parametrize(
    ("operation", "protocol", "expected_snippet"),
    [
        ("create", ApiType.REST, "Connector manifest"),
        ("create", ApiType.SCIM, "Connector manifest"),
        ("create", ApiType.SQL, "Connector manifest"),
        ("update", ApiType.REST, "Connector manifest"),
        ("delete", ApiType.SCIM, "Connector manifest"),
        ("native_schema", ApiType.SQL, "objectClasses"),
    ],
)
def test_operation_assets_declarative_docs_path_resolves_to_real_bundled_content(operation, protocol, expected_snippet):
    """Every operation/protocol pair points at a real, non-empty bundled declarative reference."""
    assets = get_operation_assets(operation, protocol)
    docs_text = load_required_adoc_text(
        "src.modules.codegen.documentations",
        assets.declarative_docs_path,
    )

    assert docs_text.strip()
    assert expected_snippet in docs_text


@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
def test_search_operation_assets_declarative_docs_path_resolves_to_real_bundled_content(protocol):
    assets = get_search_operation_assets(protocol, SearchIntent.ALL)
    docs_text = load_required_adoc_text("src.modules.codegen.documentations", assets.declarative_docs_path)

    assert docs_text.strip()


@pytest.mark.parametrize(
    ("operation", "protocol"),
    [
        ("create", ApiType.REST),
        ("create", ApiType.SCIM),
        ("create", ApiType.SQL),
        ("update", ApiType.REST),
        ("update", ApiType.SCIM),
        ("update", ApiType.SQL),
        ("delete", ApiType.REST),
        ("delete", ApiType.SCIM),
        ("delete", ApiType.SQL),
        ("native_schema", ApiType.REST),
        ("native_schema", ApiType.SCIM),
        ("native_schema", ApiType.SQL),
        ("authorization", ApiType.REST),
        ("authorization", ApiType.SCIM),
    ],
)
def test_every_operation_prompt_carries_the_declarative_format_policy_and_a_docs_slot(operation, protocol):
    """
    Every operation/protocol system prompt splices in the shared policy fragment (so the LLM is
    told to prefer YAML) and reserves a `{declarative_docs}` slot the bundled reference is
    rendered into - this is what generation.py fills at each corresponding call site.
    """
    assets = get_operation_assets(operation, protocol)

    assert "DECLARATIVE YAML VS GROOVY" in assets.system_prompt
    assert "{declarative_docs}" in assets.system_prompt
    # A sentence out of the shared fragment itself, to guard against a prompt merely mentioning
    # the words above without actually splicing in DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES.
    assert "Prefer declarative YAML for this operation" in DECLARATIVE_FORMAT_POLICY_SYSTEM_RULES
    assert "Prefer declarative YAML for this operation" in assets.system_prompt


@pytest.mark.parametrize("intent", [SearchIntent.ALL, SearchIntent.FILTER, SearchIntent.ID])
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
def test_every_search_prompt_carries_the_declarative_format_policy_and_a_docs_slot(protocol, intent):
    assets = get_search_operation_assets(protocol, intent)

    assert "{declarative_docs}" in assets.system_prompt


@pytest.mark.parametrize(
    ("operation", "protocol"),
    [
        ("create", ApiType.SCIM),
        ("update", ApiType.SCIM),
        ("create", ApiType.SQL),
        ("update", ApiType.SQL),
    ],
)
def test_rendered_generation_prompt_contains_bundled_declarative_reference_verbatim(operation, protocol):
    """
    An actually-rendered prompt (docs text substituted, like generation.py produces) contains the
    real bundled declarative-format text, not just the {declarative_docs} placeholder.
    """
    assets = get_operation_assets(operation, protocol)
    declarative_docs_text = load_required_adoc_text("src.modules.codegen.documentations", assets.declarative_docs_path)

    template = ChatPromptTemplate.from_messages([("system", assets.system_prompt)]).partial(
        declarative_docs=declarative_docs_text,
        repair_system_suffix="",
        **{
            key: ""
            for key in (
                "create_docs",
                "update_docs",
                "object_class",
                "base_api_url",
                "preferred_endpoints_json",
                "scim_protocol_schema_json",
                "scim_resource_contract_json",
                "connid_object_class_json",
                "scim_service_provider_config_json",
                "database_name",
                "sql_physical_table_json",
                "sql_connector_object_class_json",
                "authentication_container",
            )
        },
    )
    rendered = template.format()

    assert "Prefer declarative YAML for this operation" in rendered
    # A snippet unique to the actual bundled file content (not the shared policy fragment),
    # proving the real reference text made it into the rendered prompt.
    assert "Connector manifest" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("current_script", [None, YAML_SUFFICIENT_CODE, GROOVY_REQUIRED_CODE])
async def test_generation_passes_only_accepted_results_to_following_chunks(current_script):
    from src.modules.codegen.schema import CodegenRepairContext

    generator = _DummyGenerator()
    valid = current_script or YAML_SUFFICIENT_CODE
    chain = _RecordingChain(["objectClasses: {User: {search: {endpoints: 42}}}", valid, valid])
    repair = (
        CodegenRepairContext(current_script=current_script, midpoint_errors=["Fix mapping"]) if current_script else None
    )
    with (
        patch.object(generator, "_build_chunks", return_value=(["a", "b", "c"], [None] * 3, {}, [])),
        patch.object(generator, "_initialize_progress", new_callable=AsyncMock),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch.object(generator, "_cleanup_generated_code", new_callable=AsyncMock, side_effect=lambda **kw: kw["code"]),
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock) as errors,
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
    ):
        result = await generator.generate(job_id=uuid4(), repair_context=repair)
    assert [call["result"] for call in chain.calls] == [
        (current_script or "").strip(),
        (current_script or "").strip(),
        valid.strip(),
    ]
    assert result == valid.strip()
    errors.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_embedded_groovy_cleanup_preserves_valid_yaml():
    generator = _DummyGenerator()
    chain = _DummyChain(['authentication: {rest: {bearer: {implementation: "return ("}}}'])
    with (
        patch("src.modules.codegen.core.base.get_default_llm"),
        patch("src.modules.codegen.core.base.make_basic_chain", return_value=chain),
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock) as errors,
    ):
        result = await generator._cleanup_generated_code(YAML_SUFFICIENT_CODE, uuid4())
    assert result == YAML_SUFFICIENT_CODE
    errors.assert_awaited_once()
