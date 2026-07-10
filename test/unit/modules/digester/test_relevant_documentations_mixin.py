# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Characterization tests for the shared relevantDocumentations behavior.

These lock the validation/serialization contract of the ``relevant_documentations``
field across every model that carries it, so the consolidation onto a single
``RelevantDocumentationsMixin`` stays behavior-preserving.
"""

import pytest

from src.modules.digester.enums import ConfidenceLevel, EndpointMethod
from src.modules.digester.schemas import (
    AttributeProcessingInfo,
    ConnectivityEndpointInfo,
    EndpointInfo,
    FinalObjectClass,
)

CASES = [
    (EndpointInfo, {"path": "/users", "method": EndpointMethod.GET, "description": "d"}),
    (ConnectivityEndpointInfo, {"path": "/health", "method": EndpointMethod.GET, "description": "d"}),
    (FinalObjectClass, {"name": "User", "description": "d", "confidence": ConfidenceLevel.HIGH}),
    (AttributeProcessingInfo, {"name": "id", "relevant_sequences": []}),
]


@pytest.mark.parametrize("model,base", CASES)
def test_camel_case_refs_roundtrip(model, base):
    obj = model.model_validate({**base, "relevantDocumentations": [{"docId": "d1", "chunkId": "c1"}]})

    # Internally stored as snake_case dicts.
    assert obj.relevant_documentations == [{"chunk_id": "c1", "doc_id": "d1"}]

    # Serialized back to camelCase docId/chunkId.
    dumped = obj.model_dump(by_alias=True)
    assert dumped["relevantDocumentations"] == [{"docId": "d1", "chunkId": "c1"}]


@pytest.mark.parametrize("model,base", CASES)
def test_snake_case_input_accepted_and_incomplete_refs_dropped(model, base):
    obj = model.model_validate(
        {
            **base,
            "relevant_documentations": [
                {"doc_id": "d2", "chunk_id": "c2"},
                {"doc_id": "d3"},  # missing chunk id -> dropped
            ],
        }
    )

    dumped = obj.model_dump(by_alias=True)
    assert dumped["relevantDocumentations"] == [{"docId": "d2", "chunkId": "c2"}]


@pytest.mark.parametrize("model,base", CASES)
def test_defaults_to_empty_list(model, base):
    obj = model.model_validate(dict(base))
    assert obj.relevant_documentations == []
    assert obj.model_dump(by_alias=True)["relevantDocumentations"] == []
