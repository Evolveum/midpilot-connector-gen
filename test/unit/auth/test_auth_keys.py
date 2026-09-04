# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import hashlib

from src.auth.keys import (
    API_KEY_FORMAT_PREFIX,
    KEY_PREFIX_LENGTH,
    generate_api_key,
    hash_api_key,
    matches_master_key,
)


def test_generated_key_format_and_hashing():
    """One issue covers the whole storage contract: format, prefix, digest, and freshness.

    ``hash_api_key`` is asserted here rather than separately - it is the same function that
    must reproduce ``generated.hash`` from the key value on every authenticated request.
    """
    first = generate_api_key()
    second = generate_api_key()

    assert first.value.startswith(API_KEY_FORMAT_PREFIX)
    assert first.prefix == first.value[:KEY_PREFIX_LENGTH]
    assert first.hash == hashlib.sha256(first.value.encode("utf-8")).hexdigest()
    assert len(first.hash) == 64

    # Recomputing the digest from the presented value is what authentication does.
    assert hash_api_key(first.value) == first.hash
    assert hash_api_key("mpcg_abc") != hash_api_key("mpcg_abd")

    # Each issued key is distinct, so generation can never be made deterministic.
    assert first.value != second.value
    assert first.hash != second.hash


def test_matches_master_key():
    assert matches_master_key("master-secret", "master-secret")
    assert not matches_master_key("other-secret", "master-secret")
    assert not matches_master_key("", "master-secret")
