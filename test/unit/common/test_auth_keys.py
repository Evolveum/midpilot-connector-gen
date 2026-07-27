# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import hashlib

from src.common.auth.keys import (
    API_KEY_FORMAT_PREFIX,
    KEY_PREFIX_LENGTH,
    generate_api_key,
    hash_api_key,
    matches_master_key,
)


def test_generated_key_has_expected_format():
    generated = generate_api_key()

    assert generated.value.startswith(API_KEY_FORMAT_PREFIX)
    assert generated.prefix == generated.value[:KEY_PREFIX_LENGTH]
    assert generated.hash == hashlib.sha256(generated.value.encode("utf-8")).hexdigest()
    assert len(generated.hash) == 64


def test_generated_keys_are_unique():
    first = generate_api_key()
    second = generate_api_key()

    assert first.value != second.value
    assert first.hash != second.hash


def test_hash_api_key_is_deterministic():
    assert hash_api_key("mpcg_abc") == hash_api_key("mpcg_abc")
    assert hash_api_key("mpcg_abc") != hash_api_key("mpcg_abd")


def test_matches_master_key():
    assert matches_master_key("master-secret", "master-secret")
    assert not matches_master_key("other-secret", "master-secret")
    assert not matches_master_key("", "master-secret")
