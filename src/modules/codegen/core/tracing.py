# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.


def generation_run_name(base_name: str, *, repair: bool) -> str:
    """Distinguish a repair from generation without changing the operation's base name."""
    return f"{base_name}:Repair" if repair else base_name
