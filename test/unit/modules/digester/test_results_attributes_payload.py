# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.modules.digester.results import _select_attributes_payload, store_attributes_override


def test_wrapped_attributes_map_is_unwrapped():
    payload = {
        "attributes": {"id": {"type": "string"}},
        "relevantDocumentations": [{"docId": "d", "chunkId": "c"}],
    }
    assert _select_attributes_payload(payload) == {"id": {"type": "string"}}


def test_direct_attribute_map_from_override_is_preserved():
    """A direct attribute map (produced by a PUT override) must be kept as-is."""
    payload = {"id": {"type": "string"}, "name": {"type": "string"}}
    assert _select_attributes_payload(payload) == {"id": {"type": "string"}, "name": {"type": "string"}}


def test_malformed_wrapped_payload_does_not_leak_wrapper_keys():
    """A wrapped payload whose attributes field is malformed must not leak wrapper keys."""
    payload = {"attributes": None, "relevantDocumentations": [{"docId": "d", "chunkId": "c"}]}
    assert _select_attributes_payload(payload) == {}


@pytest.mark.asyncio
async def test_attributes_override_preserves_internal_scim_codegen_context():
    session_id = uuid4()
    repo = MagicMock()
    repo.get_session_data = AsyncMock(
        return_value={
            "attributes": {"userName": {"type": "string"}},
            "scimContext": {"resource": {"endpoint": "/Users"}},
        }
    )

    with (
        patch("src.modules.digester.results.get_session_documentation", new_callable=AsyncMock, return_value=[]),
        patch("src.modules.digester.results._store_result_with_relevance", new_callable=AsyncMock) as store_result,
    ):
        await store_attributes_override(
            MagicMock(),
            repo,
            session_id,
            "user",
            {"userName": {"type": "string", "mandatory": True}},
        )

    stored_payload = store_result.await_args.args[4]
    assert stored_payload == {
        "attributes": {"userName": {"type": "string", "mandatory": True}},
        "scimContext": {"resource": {"endpoint": "/Users"}},
    }
