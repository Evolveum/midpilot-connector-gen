# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.digester.results import _select_attributes_payload


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
