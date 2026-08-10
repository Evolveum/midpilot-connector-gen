# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

sort_sql_object_classes_system_prompt = textwrap.dedent(
    """
    You are ranking database-backed object classes by IGA/IDM primacy within one
    confidence bucket. Each input contains only its exact name and a compact
    description used as a ranking hint.

    Put the most central and first-class IGA/IDM entities first. Prioritize
    identities/accounts, groups, organizations, roles/entitlements,
    memberships/assignments, and other broadly referenced access concepts.
    Prefer canonical/base classes over technical, partition, helper, audit,
    scheduler, or per-resource storage tables. If uncertain, keep the original
    relative order.

    Return every exact input name once in the requested order. Do not return
    descriptions or invent, edit, merge, or drop names. Output only the
    structured response.
    """
)


sort_sql_object_classes_user_prompt = textwrap.dedent(
    """
    Confidence bucket: {confidence_level}

    <objectClasses>
    {items_json}
    </objectClasses>

    Return only the ordered object-class names using the structured schema.
    """
)
