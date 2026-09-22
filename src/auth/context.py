# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from dataclasses import dataclass

from src.config.auth import AuthMode


@dataclass(frozen=True)
class AuthContext:
    """Request identity; stores only a fingerprint, never the forwarded secret."""

    mode: AuthMode
    owner_key_hash: str | None = None

    def can_access_session(self, owner_key_hash: str | None) -> bool:
        if self.mode is AuthMode.dev:
            return True
        return owner_key_hash is not None and owner_key_hash == self.owner_key_hash
