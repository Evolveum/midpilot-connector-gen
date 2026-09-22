# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import hashlib


def hash_api_key(value: str) -> str:
    """Fingerprint a high-entropy gateway-issued key for session ownership."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
