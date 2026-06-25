# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Shared prompt fragments for the apiType detection signals.

Centralizes the REST/SCIM/SQL definitions so the per-chunk, knowledge, and web-search
prompts stay consistent and cannot drift apart. The block must not contain any ``{``/``}``
characters, as it is embedded verbatim into ChatPromptTemplate system prompts.
"""

import textwrap

API_TYPE_DEFINITIONS = textwrap.dedent(
    """
    Definitions:
    - SCIM = the SCIM identity-provisioning standard (RFC 7643/7644), regardless of how it is transported.
      SCIM is ALWAYS delivered over HTTP and is RESTful by design, so phrases like "SCIM API is RESTful",
      "SCIM REST API", or "RESTful SCIM endpoints" still mean SCIM — the "REST/RESTful" word only names the
      transport, while SCIM is the actual protocol. Signals: the term "SCIM"; standardized resources such as
      /Users and /Groups; /ServiceProviderConfig, /Schemas, /ResourceTypes; SCIM core schema URNs
      (e.g. "urn:ietf:params:scim:..."); SCIM filter syntax. If any of these appear, classify as SCIM
      (NOT REST), even when the text also calls it REST/RESTful/HTTP.
    - REST = the application's OWN custom/proprietary HTTP API that does NOT follow the SCIM standard.
      Treat OpenAPI/Swagger specifications as REST. Use REST only for a vendor-defined resource model, not
      for SCIM endpoints.
    - SQL = direct database/schema/table integration (e.g. JDBC/ODBC connection strings, SQL queries,
      table/schema definitions) with no HTTP API layer.
    """
).strip()
