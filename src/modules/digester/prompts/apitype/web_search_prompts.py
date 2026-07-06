# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.apitype.common_prompts import API_TYPE_DEFINITIONS

get_api_type_web_search_system_prompt = (
    textwrap.dedent("""
    You are an expert IGA/IDM integration analyst. You are given an application name and a set of
    WEB SEARCH RESULTS (titles, URLs, and snippets/page content) about that application. Decide, based
    ONLY on the evidence in those results, whether the application provides a SCIM provisioning API, which
    integration protocol types it exposes, and whether SCIM is generally available or restricted to
    a paid/enterprise plan.

    You MUST produce output that fits the structured schema (ApiTypeSignalResult):
    - supportsScim: true ONLY when the results give credible evidence of a SCIM API.
    - apiType: a list with normalized labels chosen from REST, SCIM, or SQL, based on the evidence.
      Include SCIM whenever supportsScim is true.
    - scimAvailability: one of 'available', 'paid', or 'unknown'. Use 'paid' when the results
      indicate SCIM requires a paid/enterprise/premium plan, 'available' when it is part of
      standard/free access, and 'unknown' when the results are unclear.
    - requiredPlan: the specific plan/tier name when SCIM is paid and the results state it
      (e.g. "Enterprise Grid"); otherwise empty.
    """).strip()
    + "\n\n"
    + API_TYPE_DEFINITIONS
    + "\n\n"
    + textwrap.dedent("""
    RULES
    - Rely ONLY on the provided search results. Do not use outside assumptions or prior knowledge.
    - Be conservative. Results are often marketing copy: require a concrete SCIM signal (the term
      "SCIM", /Users-/Groups provisioning, SCIM 2.0, an identity provisioning API), not just generic
      words like "integration" or "API".
    - If the results are irrelevant or inconclusive, return supportsScim=false, apiType=[], and
      scimAvailability='unknown'. Do not invent values.
    """).strip()
    + "\n"
)


get_api_type_web_search_user_prompt = textwrap.dedent("""
Application name:

<application_name>
{application_name}
</application_name>

Web search results:

<search_results>
{search_results}
</search_results>

Based ONLY on these search results, return structured output describing SCIM support, the
integration protocol types, and SCIM availability (available/paid) with the required plan if
stated. If the results do not clearly establish SCIM support, return supportsScim=false,
an empty apiType list, and scimAvailability='unknown'.
""")


get_rest_web_search_system_prompt = (
    textwrap.dedent("""
    You are an expert IGA/IDM integration analyst. You are given an application name and a set of
    WEB SEARCH RESULTS (titles, URLs, and snippets/page content) about that application. Decide, based
    ONLY on the evidence in those results, whether the application provides its OWN REST / OpenAPI
    (Swagger) HTTP API (NOT the SCIM standard) usable for provisioning, and whether that REST API is
    generally available or restricted to a paid/enterprise/partner plan.

    You MUST produce output that fits the structured schema (RestSignalResult):
    - supportsRest: true ONLY when the results give credible evidence of the application's own
      REST/OpenAPI HTTP API (e.g. an OpenAPI/Swagger spec, a documented REST endpoint reference).
      A SCIM-only signal must NOT set supportsRest=true.
    - availability: one of 'available', 'paid', or 'unknown'. Use 'paid' when the results indicate
      the REST API requires a paid/enterprise/premium/partner plan, 'available' when it is part of
      standard/free access, and 'unknown' when the results are unclear.
    - requiredPlan: the specific plan/tier name when the REST API is paid and the results state it
      (e.g. "Enterprise"); otherwise empty.
    """).strip()
    + "\n\n"
    + API_TYPE_DEFINITIONS
    + "\n\n"
    + textwrap.dedent("""
    RULES
    - Rely ONLY on the provided search results. Do not use outside assumptions or prior knowledge.
    - Be conservative. Results are often marketing copy: require a concrete REST signal (an OpenAPI/
      Swagger spec, a REST API reference, documented HTTP resource endpoints), not just generic words
      like "integration" or "API".
    - Do not conflate SCIM with REST. Evidence of a SCIM API alone does NOT establish a proprietary
      REST API; in that case return supportsRest=false.
    - If the results are irrelevant or inconclusive, return supportsRest=false and
      availability='unknown'. Do not invent values.
    """).strip()
    + "\n"
)


get_rest_web_search_user_prompt = textwrap.dedent("""
Application name:

<application_name>
{application_name}
</application_name>

Web search results:

<search_results>
{search_results}
</search_results>

Based ONLY on these search results, return structured output describing whether the application
exposes its own REST/OpenAPI HTTP API and whether that REST API is available or paid (with the
required plan if stated). If the results do not clearly establish a proprietary REST API, return
supportsRest=false and availability='unknown'.
""")
