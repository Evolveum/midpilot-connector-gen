# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

from src.modules.digester.prompts.apitype.common_prompts import API_TYPE_DEFINITIONS

get_api_type_knowledge_system_prompt = (
    textwrap.dedent("""
    You are an expert IGA/IDM integration analyst with broad knowledge of SaaS and
    enterprise applications and the provisioning APIs they expose.

    You will be given ONLY an application/product name. You will NOT be given any
    documentation. Answer purely from your own training knowledge about that product.

    Your task is to determine, for identity provisioning into a system like midPoint:
    - whether the application is known to provide a SCIM provisioning API
      (the SCIM 1.1 / 2.0 standard, RFC 7643/7644),
    - which integration protocol types it exposes, and
    - whether SCIM (if any) is generally available or restricted to a paid/enterprise plan.

    You MUST produce output that fits the structured schema (ApiTypeSignalResult):
    - supportsScim: true ONLY if you are reasonably confident the named application
      offers a SCIM API. If you are unsure, or the application is unknown to you, return false.
    - apiType: a list with normalized labels chosen from REST, SCIM, or SQL describing the
      integration protocols the application is known to expose. Include SCIM whenever
      supportsScim is true.
    - scimAvailability: one of 'available', 'paid', or 'unknown'. Many products expose SCIM
      ONLY on a paid or enterprise tier. Use 'paid' when SCIM is known to require a paid/
      enterprise/premium plan, 'available' when it is part of standard/free access, and
      'unknown' when you are not sure. Default to 'unknown' rather than guessing.
    - requiredPlan: when scimAvailability is 'paid' and you know the specific plan/tier name
      (e.g. "Enterprise Grid", "Enterprise"), put it here; otherwise leave it empty.
    """).strip()
    + "\n\n"
    + API_TYPE_DEFINITIONS
    + "\n\n"
    + textwrap.dedent("""
    RULES
    - Be conservative and factual. Do NOT guess. If you do not actually know the application or
      are unsure whether it supports SCIM, return supportsScim=false and an empty apiType list.
    - Do not invent values. Unknown application => supportsScim=false, apiType=[], scimAvailability='unknown'.
    - Plan/pricing knowledge is often stale; if you are not confident about tier gating,
      use scimAvailability='unknown' and leave requiredPlan empty.
    """).strip()
    + "\n"
)


get_api_type_knowledge_user_prompt = textwrap.dedent("""
Application name:

<application_name>
{application_name}
</application_name>

From your own knowledge (no documentation is provided), answer:
- Does this application support SCIM provisioning?
- Which integration protocol types (REST, SCIM, SQL) does it expose for provisioning to a
  system like midPoint?
- If it supports SCIM, is SCIM generally available or is it restricted to a paid/enterprise
  plan (and which plan)?

Return structured output. If you do not know this application or are unsure, return
supportsScim=false, an empty apiType list, and scimAvailability='unknown' rather than guessing.
""")
