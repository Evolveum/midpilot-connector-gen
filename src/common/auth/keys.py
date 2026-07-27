# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""API key generation and hashing.

Keys are random secrets with high entropy, so a single unsalted SHA-256 is a
sufficient storage hash (no KDF needed). Only the hash is ever persisted; the
full key value leaves the service exactly once, in the issue response.
"""

import hashlib
import secrets
from dataclasses import dataclass

API_KEY_FORMAT_PREFIX = "mpcg_"

# Length of the stored/displayed key prefix (includes the format prefix).
# Long enough to identify a key in listings and logs, far too short to guess
# the remaining secret from.
KEY_PREFIX_LENGTH = 10


@dataclass(frozen=True)
class GeneratedApiKey:
    """A freshly generated API key: full value, display prefix and storage hash."""

    value: str
    prefix: str
    hash: str


def hash_api_key(value: str) -> str:
    """Return the SHA-256 hex digest used to store and look up an API key."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_api_key() -> GeneratedApiKey:
    """Generate a new random API key in the ``mpcg_<token>`` format."""
    value = API_KEY_FORMAT_PREFIX + secrets.token_urlsafe(32)
    return GeneratedApiKey(value=value, prefix=value[:KEY_PREFIX_LENGTH], hash=hash_api_key(value))


def matches_master_key(presented_key: str, master_key: str) -> bool:
    """Constant-time comparison of a presented key against the configured master key.

    Comparing the hashes keeps the comparison constant-time regardless of the
    lengths of the two values.
    """
    return secrets.compare_digest(hash_api_key(presented_key), hash_api_key(master_key))
