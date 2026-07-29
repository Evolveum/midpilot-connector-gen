# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import datetime, timezone
from enum import Enum

from src.shared.json_values import to_jsonable
from src.shared.normalize import normalized_input_fingerprint


class _ExampleEnum(Enum):
    value = "example"


def test_fingerprint_is_stable_across_json_persistence() -> None:
    raw_payload = {
        "timestamp": datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc),
        "enum": _ExampleEnum.value,
        "unordered": {"beta", "alpha"},
    }
    persisted_payload = to_jsonable(raw_payload)

    assert normalized_input_fingerprint(raw_payload) == normalized_input_fingerprint(persisted_payload)
