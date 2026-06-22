# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
apiType detection subsystem.

Hosts the individual signals used to determine the API protocol (REST/SCIM/SQL)
and the logic that combines them. The first signal is the scim.cloud registry
lookup; further signals (web search, endpoint structure, availability) build on
the same structure.
"""
