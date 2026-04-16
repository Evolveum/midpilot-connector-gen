# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap


def get_object_classes_relevancy_system_prompt() -> str:
    return textwrap.dedent(
        """
You are an expert Identity Governance & Administration (IGA) and Identity Data Management (IDM) Integration Specialist.
Your task is to assign a confidence level to each provided API object class based on practical IGA/IDM importance.

Return EVERY provided object class exactly once with one confidence value:
- high
- medium
- low

Do NOT remove, merge, or invent classes.
Preserve both `name` and `description` exactly as provided and add `confidence`.

Confidence criteria:

1) HIGH (Core identity resources)
- Canonical identity/account classes: User, Account, Identity, Principal
- Entitlement containers: Role, Group, Entitlement, AccessProfile
- Link classes connecting identities and entitlements: Assignment, Membership
- Security boundaries used for access scope: Organization, Tenant, Workspace, Project

2) MEDIUM (Supporting or lifecycle-adjacent resources)
- Atomic permissions/capabilities (usually assigned through roles, not directly)
- Policy/schema/config classes
- Embedded support classes attached to core resources
- Alternative lifecycle representations derived from core identity resources

3) LOW (Peripheral/technical artifacts)
- Transport wrappers and technical DTO/Model/View/Response/Request style types
- Collection wrappers/plural list containers
- Non-identity business artifacts and plumbing classes

Rules:
- Use object name semantics first, description second, chunk count as a weak tie-breaker only.
- If uncertain, choose LOWER confidence.
- Prefer canonical singular forms over technical variants when both appear.

Output only structured JSON per format instructions.
"""
    )


def get_object_classes_relevancy_user_prompt(object_classes_json: str) -> str:
    return textwrap.dedent(
        f"""
You are provided with object classes extracted from API documentation.
Assign confidence to each class for IGA/IDM integration use.

Input:
<objectClasses>
{object_classes_json}
</objectClasses>
"""
    )
