# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import UUID


class AuthMode(str, Enum):
    """How the current request was authenticated."""

    disabled = "disabled"  # AUTH__API_KEY_REQUIRED is off; no checks apply
    master = "master"  # authenticated with the configured master key
    api_key = "api_key"  # authenticated with an issued API key


@dataclass(frozen=True)
class AuthContext:
    """Authentication result attached to each request (``request.state.auth``)."""

    mode: AuthMode
    api_key_id: Optional[UUID] = None

    def can_access_session(self, owner_api_key_id: Optional[UUID]) -> bool:
        """Whether this context may access a session owned by ``owner_api_key_id``.

        Master key and disabled mode access everything. A regular API key only
        accesses sessions it owns; ownerless sessions (owner None) are
        reserved for the master key.
        """
        if self.mode in (AuthMode.disabled, AuthMode.master):
            return True
        return owner_api_key_id is not None and owner_api_key_id == self.api_key_id
