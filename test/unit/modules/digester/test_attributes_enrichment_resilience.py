# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from src.modules.digester.extractors.rest.attributes import (
    _build_attr_from_sequences,
    build_attributes_from_sequences,
)
from src.modules.digester.schemas import AttributeProcessingInfo
from src.modules.digester.schemas.common import DocProcessingSequenceItem


class _DummyResponse(BaseModel):
    type: Optional[str] = None


@pytest.mark.asyncio
async def test_unexpected_llm_result_type_keeps_attribute():
    """An unparseable LLM enrichment result must not discard the whole attribute.

    Regression: an unexpected return type made ``_build_attr_from_sequences``
    return None, so the attribute was filtered out downstream even though it was
    already fully discovered; the exception path kept it.
    """
    attr = _attribute_with_sequence()

    # object() is neither the response model, nor a dict, nor has string content.
    with patch(
        "src.modules.digester.extractors.rest.attributes.invoke_llm",
        new=AsyncMock(return_value=object()),
    ):
        result = await _build_attr_from_sequences(
            chain=MagicMock(),
            object_class="User",
            attr=attr,
            use_steps=False,
            fields_to_update=["type"],
            log_stage="TypeFormat",
            run_name="Digester:Attributes:TypeFormat",
            response_model=_DummyResponse,
            context_builder=lambda a, s: {},
        )

    assert result is attr
    assert result.name == "id"
    assert result.type == "string"


@pytest.mark.asyncio
async def test_enrichment_stages_are_traced_under_their_own_names():
    """Each enrichment stage names its LLM calls; unnamed calls show up as ``RunnableSequence``."""
    invoke_llm = AsyncMock(return_value=object())
    with (
        patch("src.modules.digester.extractors.rest.attributes._build_type_format_chain", return_value=MagicMock()),
        patch("src.modules.digester.extractors.rest.attributes._build_boolean_flags_chain", return_value=MagicMock()),
        patch("src.modules.digester.extractors.rest.attributes.invoke_llm", new=invoke_llm),
    ):
        await build_attributes_from_sequences([_attribute_with_sequence()], object_class="User")

    assert [call.kwargs["config"]["run_name"] for call in invoke_llm.await_args_list] == [
        "Digester:Attributes:TypeFormat",
        "Digester:Attributes:BooleanFlags",
    ]


def _attribute_with_sequence() -> AttributeProcessingInfo:
    return AttributeProcessingInfo(
        name="id",
        type="string",
        relevant_sequences=[
            DocProcessingSequenceItem(
                chunk_id="c1",
                start_sequence="begin",
                end_sequence="end",
                text="id is a string",
            )
        ],
    )
