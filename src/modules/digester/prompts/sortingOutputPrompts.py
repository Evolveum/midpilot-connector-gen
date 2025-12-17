# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import textwrap

# Sort ingObject classes
sort_object_classes_system_prompt = textwrap.dedent("""
    <instruction>
    You are ranking extracted domain object classes by **IGA/IDM relevance**.
    You receive:
      - A flat list of object class names (already deduplicated).

    Rank the list so that the most central, frequently-referenced, and first-class
    IGA/IDM entities come first. Use these signals:

    - Centrality to identity & access (users, groups/teams, orgs/tenants, roles/entitlements, memberships/assignments).
    - First-class schema presence: stable identifiers, own endpoints, referenced widely.
    - Cross-cutting impact (e.g., roles vs. per-resource helper types).
    - Prefer canonical/base types over views/variants (but keep exact names as given).
    - If uncertain, keep the original relative order.

    Use the structured output schema (ObjectClassesResponse with field alias 'objectClasses').
    Do not edit, invent, or drop items—only reorder the same set.
    No comments or prose.
    </instruction>
""")

sort_object_classes_user_prompt = textwrap.dedent("""
    Extracted object classes from previous LLM call:
    <items>
    {items_json}
    </items>

    Task:
    - Return the same items reordered by relevance using the structured output schema (ObjectClassesResponse).
    - Do not add/remove/modify fields; only change order.
""")

# Sorting Auth
sort_auth_system_prompt = textwrap.dedent("""
    <instruction>
    You are ranking authentication mechanisms by **practical relevance and primacy**
    as implied by the docs. You receive:
    - A list of auth mechanisms (name, type, quirks) already deduplicated.

    Ranking guidelines:
    - Follow explicit guidance in the docs (e.g., "use OAuth 2.0", "preferred", "recommended").
    - If no explicit guidance, infer from modern common practice and scope coverage
      (e.g., OAuth2 flows that cover most endpoints > bearer tokens > basic/session),
      but DO NOT invent mechanisms that are not mentioned.
    - Prefer mechanisms documented as secure, comprehensive, and widely applicable.
    - If ties remain, keep original relative order.

    Use the structured output schema (AuthResponse).
    Do not edit, invent, or drop items—only reorder the same set.
    No comments or prose.
    </instruction>
""")

sort_auth_user_prompt = textwrap.dedent("""
    Extracted authentication mechanisms from previous LLM call:
    
    <items>
    
    {items_json}
    
    </items>
    
    Task:
    - Return the same items reordered by relevance using the structured output schema (AuthResponse).
    - Do not add/remove/modify fields; only change order.
""")
