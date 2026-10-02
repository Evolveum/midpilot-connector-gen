# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Unit tests for codegen service relation generator."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.prompts import ChatPromptTemplate

from src.modules.codegen import generation
from src.modules.codegen.core.operations import RelationGenerator
from src.modules.codegen.schema import RelationCodegenContext
from src.modules.codegen.selection.docs_loader import load_operation_documentation
from src.modules.codegen.selection.protocol_selectors import get_operation_assets
from src.modules.digester.schemas import RelationsResponse
from src.shared.enums import ApiType


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", list(ApiType))
async def test_relation_generator_renders_analysis_with_current_protocol_references(protocol):
    assets = get_operation_assets("relationship", protocol)
    docs, declarative = load_operation_documentation(assets)
    context = RelationCodegenContext(
        apiType=protocol,
        kind="link_object",
        linkObjectClass="Membership",
        linkAttributes=[{"attribute": "userId", "references": "User"}],
    )
    generator = RelationGenerator(
        relation_name="user_to_group",
        docs_text=docs,
        declarative_docs_text=declarative,
        system_prompt=assets.system_prompt,
        user_prompt=assets.user_prompt,
        protocol=protocol,
        relation_context=context,
        extra_prompt_vars={"protocol": protocol.value},
    )
    prompt = ChatPromptTemplate.from_messages(
        [("system", assets.system_prompt), ("human", assets.user_prompt)]
    ).partial(**generator.config.extra_prompt_vars)
    rendered_inputs = []

    async def generate_artifact(values, **_kwargs):
        rendered_inputs.append(prompt.format(**values))
        return "{}" if protocol is ApiType.SQL else 'relationship("user_to_group") {}'

    chain = AsyncMock()
    chain.ainvoke.side_effect = generate_artifact
    with (
        patch.object(generator, "_build_chunks", return_value=(["Membership links User and Group"], [None], {}, [])),
        patch.object(generator, "_initialize_progress", new_callable=AsyncMock),
        patch.object(generator, "_build_llm_chain", return_value=chain),
        patch.object(generator, "_cleanup_generated_code", new_callable=AsyncMock, side_effect=lambda **kw: kw["code"]),
        patch("src.modules.codegen.core.base.append_job_error", new_callable=AsyncMock) as errors,
        patch("src.modules.codegen.core.base.increment_processed_documents", new_callable=AsyncMock),
    ):
        result = await generator.generate(
            job_id=uuid4(), relations=RelationsResponse(relations=[]), relation_name="user_to_group"
        )

    errors.assert_not_awaited()
    assert len(rendered_inputs) == 1
    assert '"linkObjectClass":"Membership"' in rendered_inputs[0]
    assert docs in rendered_inputs[0] and declarative in rendered_inputs[0]
    assert "relationship overrides are unsupported" in rendered_inputs[0]
    assert result == ("{}" if protocol is ApiType.SQL else 'relationship("user_to_group") {}')


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", [ApiType.REST, ApiType.SCIM, ApiType.SQL])
async def test_generate_relation(protocol):
    """Test generating relation code."""
    test_relations_payload = {
        "relations": [
            {
                "name": "project_to_membership",
                "displayName": "Project to Membership",
                "subject": "project",
                "object": "membership",
                "subjectAttribute": "memberships",
                "objectAttribute": "",
                "shortDescription": "",
            },
            {
                "name": "membership_to_principal",
                "displayName": "Membership to Principal",
                "subject": "membership",
                "object": "principal",
                "subjectAttribute": "principal",
                "objectAttribute": "",
                "shortDescription": "",
            },
        ]
    }

    with (
        patch("src.modules.codegen.selection.relevant_chunks.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.selection.relevant_chunks.RelevantChunkRepository") as mock_relevant_repository,
        patch("src.modules.codegen.generation.RelationGenerator") as mock_relation_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db = AsyncMock()
        mock_db_cm.__aenter__.return_value = mock_db

        mock_repo_instance = mock_relevant_repository.return_value
        mock_repo_instance.get_relevant_chunks_grouped_by_entity = AsyncMock(
            return_value={
                "project": [
                    {"docId": "doc-1", "chunkId": "project-chunk"},
                    {"docId": "doc-2", "chunkId": "shared-chunk"},
                ],
                "membership": [
                    {"docId": "doc-2", "chunkId": "shared-chunk"},
                    {"docId": "doc-3", "chunkId": "membership-chunk"},
                ],
                "principal": [
                    {"docId": "doc-4", "chunkId": "principal-chunk"},
                ],
            }
        )

        # Mock the generator instance and its generate method (must be async)
        mock_generator_instance = mock_relation_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked relation code")

        relations_model = RelationsResponse.model_validate(test_relations_payload)

        result = await generation.generate_relation_code(
            relations=relations_model,
            relation_name="project_to_membership",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=protocol,
        )

        assert isinstance(result, dict)
        assert "code" in result
        assert result["code"] == "mocked relation code"

        # Verify generator was instantiated and generate method was called
        mock_relation_generator_class.assert_called_once()
        config = mock_relation_generator_class.call_args.kwargs
        assert config["extra_prompt_vars"]["protocol"] == protocol.value
        expected_reference = {
            ApiType.REST: "The relationship concept",
            ApiType.SCIM: "SCIM",
            ApiType.SQL: "Multitable support",
        }[protocol]
        assert expected_reference in config["docs_text"]
        mock_generator_instance.generate.assert_called_once()
        generate_kwargs = mock_generator_instance.generate.await_args.kwargs
        assert generate_kwargs["relation_name"] == "project_to_membership"
        assert generate_kwargs["relevant_chunk_pairs"] == [
            {"doc_id": "doc-1", "chunk_id": "project-chunk"},
            {"doc_id": "doc-2", "chunk_id": "shared-chunk"},
            {"doc_id": "doc-3", "chunk_id": "membership-chunk"},
        ]
        assert mock_relation_generator_class.call_args.kwargs["relation_context"] is None


@pytest.mark.asyncio
async def test_generate_relation_loads_the_association_class_documentation():
    """A relation carried by a third class generates with that class's chunks and context."""
    relations_model = RelationsResponse.model_validate(
        {
            "relations": [
                {
                    "name": "user_to_group",
                    "displayName": "User to Group",
                    "subject": "user",
                    "object": "group",
                    "subjectAttribute": "",
                    "objectAttribute": "",
                    "shortDescription": "",
                }
            ]
        }
    )
    relation_context = RelationCodegenContext(
        kind="link_object",
        linkObjectClass="Membership",
        linkAttributes=[{"attribute": "userId", "references": "user"}],
    )

    with (
        patch("src.modules.codegen.selection.relevant_chunks.async_session_maker") as mock_session_maker,
        patch("src.modules.codegen.selection.relevant_chunks.RelevantChunkRepository") as mock_relevant_repository,
        patch("src.modules.codegen.generation.RelationGenerator") as mock_relation_generator_class,
    ):
        mock_db_cm = mock_session_maker.return_value
        mock_db_cm.__aenter__.return_value = AsyncMock()

        mock_repo_instance = mock_relevant_repository.return_value
        mock_repo_instance.get_relevant_chunks_grouped_by_entity = AsyncMock(
            return_value={
                "user": [{"docId": "doc-1", "chunkId": "user-chunk"}],
                "group": [{"docId": "doc-2", "chunkId": "group-chunk"}],
                "membership": [{"docId": "doc-3", "chunkId": "membership-chunk"}],
            }
        )

        mock_generator_instance = mock_relation_generator_class.return_value
        mock_generator_instance.generate = AsyncMock(return_value="mocked relation code")

        await generation.generate_relation_code(
            relations=relations_model,
            relation_name="user_to_group",
            session_id=uuid4(),
            job_id=uuid4(),
            protocol=ApiType.REST,
            relation_context=relation_context,
        )

        assert mock_relation_generator_class.call_args.kwargs["relation_context"] is relation_context
        assert mock_generator_instance.generate.await_args.kwargs["relevant_chunk_pairs"] == [
            {"doc_id": "doc-1", "chunk_id": "user-chunk"},
            {"doc_id": "doc-2", "chunk_id": "group-chunk"},
            {"doc_id": "doc-3", "chunk_id": "membership-chunk"},
        ]
