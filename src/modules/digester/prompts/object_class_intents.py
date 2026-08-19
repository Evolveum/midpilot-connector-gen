# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Per-intent object-class vocabulary shared by extraction and ranking prompts.

Object-class detection is judged through a business-domain lens (`GenerationIntent`):
``management`` (IGA/IDM entities), ``itsm`` (service-management entities), or
``management_itsm`` (both domains). The lens changes what an extraction prompt treats as
a primary bucket and what the ranking prompts treat as core/supporting/peripheral - it
does not change which documentation is read or add a separate pipeline per intent.

Centralizing the wording here means every prompt that needs an intent-specific section
(REST/SCIM extraction, confidence assignment, REST/SCIM/SQL sorting) picks the one
relevant `ObjectClassIntentProfile` block instead of duplicating domain vocabulary, and a
future intent only adds one profile entry here.
"""

import textwrap
from dataclasses import dataclass
from typing import Mapping

from src.shared.enums import GenerationIntent


@dataclass(frozen=True)
class ObjectClassIntentProfile:
    """One intent's domain vocabulary for object-class extraction and ranking prompts."""

    persona_domain: str
    """Short domain name used in the analyst persona line, e.g. "Identity Governance & ...")."""

    rest_extraction_buckets: str
    """The intent-specific "WHAT TO EXTRACT" bucket list body for REST extraction."""

    scim_custom_guidance: str
    """The intent-specific "Custom Domain Objects" examples for SCIM extraction."""

    confidence_high: str
    confidence_medium: str
    confidence_low: str
    """HIGH/MEDIUM/LOW confidence-bucket criteria bodies for the relevancy/finalization prompt."""

    ranking_signals: str
    """Ranking-signal bullets for the REST/SCIM high-confidence sorting prompt."""

    sql_ranking_signals: str
    """Compact ranking guidance for the SQL sorting prompt (name + short description only)."""


_MANAGEMENT_PROFILE = ObjectClassIntentProfile(
    persona_domain="Identity Governance & Administration (IGA) / Identity Data Management (IDM)",
    rest_extraction_buckets=textwrap.dedent(
        """
        1) **Identity / User**
         Aliases: user, identity, account holder, principal, member, person, profile,
         userProfile, userIdentity, subject, actor, directoryUser, iamUser, team member,...

        2) **Group / Team**
         Aliases: group, team, cohort, circle, distributionList, mailingList,
         workspaceGroup,...

        3) **Organization / Org Unit / Tenant / Workspace / Project**
         Aliases: organization, org, orgUnit, tenant, company, businessUnit,
         workspace, space, project, department,...

        4) **Membership / Assignment (links between identities and containers)**
         Aliases: membership, memberOf, groupUser, groupMembership, teamMembership,
         orgMembership, assignment, affiliation, enrollment,...

        5) **Role / Entitlement / Access Profile / Permission Set**
         Aliases: role, entitlement, accessProfile, permissionSet, package, bundle,...

        6) **Permission / Policy / Rule / Scope / Grant**
         Aliases: permission, privilege, capability, right, grant, scope, policy,
         rule, constraint, guardrail,...

        7) **Credential / Auth Factor / Secret**
         Aliases: credential, password, passkey, token, apiToken, key, certificate,
         mfaFactor, otpDevice, recoveryCode,...

        8) **Attachment / File / Document / Media**  ← include thumbnails & base variants
         Aliases: attachment, file, document, content, media, binary, asset, blob,
         image, preview, thumbnail,...

        9) **Attribute / Field Definitions (custom or per-entity)**
         Aliases: attribute, field, customField, extendedAttribute, property,
         trait, schemaField (application-level), profileField, organizationField,
         userField,...
        """
    ).strip("\n"),
    scim_custom_guidance=textwrap.dedent(
        """
        2) **Additional Resource Types** - New object classes beyond User/Group
           Examples:
           - Application, App, AppInstance (application resources)
           - License, Subscription (licensing objects)
           - Role (when implemented as a separate SCIM resource, not just an attribute)
           - Custom domain objects specific to the application

        3) **Custom Domain Objects** - Application-specific IGA/IDM concepts
           Examples:
           - Workspace, Team, Organization (beyond standard Group)
           - Permission, Entitlement (if they are first-class SCIM resources)
        """
    ).strip("\n"),
    confidence_high=textwrap.dedent(
        """
        1) HIGH (Core identity resources)
        - Canonical identity/account classes: User, Account, Identity, Principal
        - Entitlement containers: Role, Group, Entitlement, AccessProfile
        - Link classes connecting identities and entitlements: Assignment, Membership
        - Security boundaries used for access scope: Organization, Tenant, Workspace, Project
        """
    ).strip("\n"),
    confidence_medium=textwrap.dedent(
        """
        2) MEDIUM (Supporting or lifecycle-adjacent resources)
        - Atomic permissions/capabilities (usually assigned through roles, not directly)
        - Policy/schema/config classes
        - Embedded support classes attached to core resources
        - Alternative lifecycle representations derived from core identity resources
        """
    ).strip("\n"),
    confidence_low=textwrap.dedent(
        """
        3) LOW (Peripheral/technical artifacts)
        - Transport wrappers and technical DTO/Model/View/Response/Request style types
        - Collection wrappers/plural list containers
        - Non-identity business artifacts and plumbing classes (e.g. tickets, WorkPackage,
          workflows, and service-management objects that are not the target of this intent)
        """
    ).strip("\n"),
    ranking_signals=textwrap.dedent(
        """
        - Centrality to identity & access (users, groups/teams, orgs/tenants, roles/entitlements, memberships/assignments).
        - First-class schema presence: stable identifiers, own endpoints, referenced widely.
        - Cross-cutting impact (e.g., roles vs. per-resource helper types).
        - Prefer canonical/base types over views/variants (but keep exact names as given).
        - If uncertain, keep the original relative order.
        """
    ).strip("\n"),
    sql_ranking_signals=(
        "Put the most central and first-class IGA/IDM entities first. Prioritize "
        "identities/accounts, groups, organizations, roles/entitlements, "
        "memberships/assignments, and other broadly referenced access concepts. "
        "Prefer canonical/base classes over technical, partition, helper, audit, "
        "scheduler, or per-resource storage tables."
    ),
)

_ITSM_PROFILE = ObjectClassIntentProfile(
    persona_domain="IT Service Management (ITSM)",
    rest_extraction_buckets=textwrap.dedent(
        """
        1) **Ticket / Issue / Case**
         Aliases: ticket, issue, case, incident, problem, changeRequest, serviceRequest,
         workOrder, workItem, workPackage,...

        2) **Queue / Project / Board (containers a ticket belongs to)**
         Aliases: queue, project, board, category, component, team (as a routing target),...

        3) **Workflow / Status / Priority / Type (process and classification objects)**
         Aliases: workflow, status, transition, priority, severity, issueType, resolution,...

        4) **Comment / Attachment / Worklog (activity recorded on a ticket)**
         Aliases: comment, note, worklog, timeEntry, attachment, changelog, history,...

        5) **SLA / Escalation / Approval**
         Aliases: sla, escalationPolicy, approval, approvalStep,...

        6) **Assignment / Watcher / Participant (links between tickets and people)**
         Aliases: assignee, watcher, participant, reporter, requester, assignment,...

        7) **Identity / Organization (only when needed to understand relationships or the API)**
         Aliases: user, agent, technician, team, group, organization, department,...
         NOTE: still extract these when they are first-class API resources - they are
         needed for relations, endpoints, and attribute context - but they are not the
         primary target of this intent. Do not omit them here; the confidence/ranking
         step (not extraction) is what decides final prioritization.
        """
    ).strip("\n"),
    scim_custom_guidance=textwrap.dedent(
        """
        2) **Additional Resource Types** - New object classes beyond User/Group
           Examples:
           - Ticket, Case, Incident, ChangeRequest, WorkPackage (service-management work
             items, when exposed as SCIM-like or adjacent custom resources)
           - Queue, Project, Board (containers a ticket belongs to)
           - Custom domain objects specific to the application

        3) **Custom Domain Objects** - Application-specific ITSM concepts
           Examples:
           - Workflow, Status, Priority, SLA (process/config objects, if first-class resources)
           - Team, Organization (beyond standard Group, when used for ticket routing)
        """
    ).strip("\n"),
    confidence_high=textwrap.dedent(
        """
        1) HIGH (Core service-management work items)
        - Canonical ticket/work-item classes: Ticket, Issue, Case, Incident, Problem,
          ChangeRequest, ServiceRequest, WorkOrder, WorkItem, WorkPackage
        - Containers a ticket is organized by: Queue, Project, Board
        - Link classes connecting tickets to people or containers: Assignment, Watcher
        """
    ).strip("\n"),
    confidence_medium=textwrap.dedent(
        """
        2) MEDIUM (Supporting or lifecycle-adjacent resources)
        - Activity recorded on a ticket: Comment, Worklog, Attachment, ChangeLog
        - Process/classification config: Workflow, Status, Priority, Severity, SLA,
          EscalationPolicy, Approval
        - Embedded support classes attached to core resources
        """
    ).strip("\n"),
    confidence_low=textwrap.dedent(
        """
        3) LOW (Peripheral/technical artifacts, and identity/management resources
        used only for relationships in this intent)
        - Transport wrappers and technical DTO/Model/View/Response/Request style types
        - Collection wrappers/plural list containers
        - Identity/management resources such as User, Group, Role, Organization: they may
          still be present and useful for relations, but are not the primary target of an
          `itsm` intent, so they rank low here even when they would rank high under a
          `management` intent
        """
    ).strip("\n"),
    ranking_signals=textwrap.dedent(
        """
        - Centrality to the service-management workflow (tickets/cases/incidents as the
          primary work item; queues/projects as containers; workflows/statuses as process state).
        - First-class schema presence: stable identifiers, own endpoints, referenced widely.
        - Cross-cutting impact (e.g., a ticket type vs. a per-resource helper type).
        - Prefer canonical/base types over views/variants (but keep exact names as given).
        - If uncertain, keep the original relative order.
        """
    ).strip("\n"),
    sql_ranking_signals=(
        "Put the most central and first-class ITSM work-item entities first. Prioritize "
        "tickets/cases/incidents/problems/changes/work orders/work packages, the "
        "queues/projects that contain them, and workflow/status/priority entities. Prefer canonical/base "
        "classes over technical, partition, helper, audit, scheduler, or per-resource "
        "storage tables. Identity/organization tables (users, groups) that exist only to "
        "support relationships should rank below the core ITSM entities."
    ),
)

_MANAGEMENT_ITSM_PROFILE = ObjectClassIntentProfile(
    persona_domain="Identity Governance & Administration (IGA/IDM) and IT Service Management (ITSM)",
    rest_extraction_buckets=textwrap.dedent(
        """
        1) **Identity / User**
         Aliases: user, identity, account holder, principal, member, person, profile,
         userProfile, userIdentity, subject, actor, directoryUser, iamUser, team member,...

        2) **Group / Team / Organization / Tenant / Workspace / Project**
         Aliases: group, team, organization, orgUnit, tenant, company, businessUnit,
         workspace, project, department,...

        3) **Membership / Assignment (links between identities and containers)**
         Aliases: membership, memberOf, groupMembership, teamMembership, orgMembership,
         assignment, affiliation, enrollment,...

        4) **Role / Entitlement / Access Profile / Permission / Policy**
         Aliases: role, entitlement, accessProfile, permissionSet, privilege, grant,
         scope, policy, rule,...

        5) **Credential / Auth Factor / Secret**
         Aliases: credential, password, passkey, token, apiToken, key, certificate,
         mfaFactor, otpDevice, recoveryCode,...

        6) **Ticket / Issue / Case / Work Package**
         Aliases: ticket, issue, case, incident, problem, changeRequest, serviceRequest,
         workOrder, workItem, workPackage,...

        7) **Queue / Project / Board (containers a work item belongs to)**
         Aliases: queue, project, board, category, component, routingTeam,...

        8) **Workflow / Status / Priority / Type**
         Aliases: workflow, status, transition, priority, severity, issueType, resolution,...

        9) **Comment / Attachment / Worklog / Activity**
         Aliases: comment, note, worklog, timeEntry, attachment, changelog, history,...

        10) **SLA / Escalation / Approval / Participant**
         Aliases: sla, escalationPolicy, approval, approvalStep, assignee, watcher,
         participant, reporter, requester,...

        11) **Attribute / Field Definitions (custom or per-entity)**
         Aliases: attribute, field, customField, extendedAttribute, property, trait,
         schemaField, profileField, organizationField, userField,...

        Extract first-class resources from either domain. Do not demote or omit a resource
        merely because it belongs primarily to the other domain; confidence assignment is
        responsible for final prioritization.
        """
    ).strip("\n"),
    scim_custom_guidance=textwrap.dedent(
        """
        2) **Additional Resource Types** - New object classes beyond User/Group
           Examples:
           - Application, License, Role, Entitlement (identity and access resources)
           - Ticket, Case, Incident, ChangeRequest, WorkPackage (service-management work items)
           - Queue, Project, Board (containers a work item belongs to)
           - Custom domain objects specific to the application

        3) **Custom Domain Objects** - Application-specific management or ITSM concepts
           Examples:
           - Workspace, Team, Organization, Permission, Entitlement
           - Workflow, Status, Priority, SLA, Assignment, Watcher
        """
    ).strip("\n"),
    confidence_high=textwrap.dedent(
        """
        1) HIGH (Core management resources and core service-management work items)
        - Canonical identity/account classes: User, Account, Identity, Principal
        - Entitlement containers: Role, Group, Entitlement, AccessProfile
        - Identity links and security boundaries: Membership, Assignment, Organization,
          Tenant, Workspace, Project
        - Canonical work-item classes: Ticket, Issue, Case, Incident, Problem,
          ChangeRequest, ServiceRequest, WorkOrder, WorkItem, WorkPackage
        - Work-item containers and links: Queue, Board, Project, Assignment, Watcher
        - Union rule: a class that is core to either management or ITSM is HIGH, even if
          the other single-domain intent would classify it as LOW
        """
    ).strip("\n"),
    confidence_medium=textwrap.dedent(
        """
        2) MEDIUM (Supporting or lifecycle-adjacent resources from either domain)
        - Atomic permissions, policy/schema/config classes, and alternative lifecycle views
        - Work-item activity: Comment, Worklog, Attachment, ChangeLog
        - Process/classification config: Workflow, Status, Priority, Severity, SLA,
          EscalationPolicy, Approval
        - Embedded support classes, unless they are a core class in either domain
        """
    ).strip("\n"),
    confidence_low=textwrap.dedent(
        """
        3) LOW (Artifacts peripheral to both management and ITSM)
        - Transport wrappers and technical DTO/Model/View/Response/Request style types
        - Collection wrappers/plural list containers
        - Technical plumbing, audit, scheduler, partition, and per-resource storage types
        - Never classify a core identity/access class or a core service-management work-item
          class as LOW under this combined intent
        """
    ).strip("\n"),
    ranking_signals=textwrap.dedent(
        """
        - Keep both management and ITSM core resources in the HIGH bucket.
        - Within HIGH, rank management resources first: identities/accounts, groups,
          organizations, roles/entitlements, and memberships/assignments.
        - Rank ITSM work items and their containers next: tickets/cases/incidents/problems,
          changes, work orders/work packages, queues/projects, and workflows/statuses.
        - Within each domain, prefer first-class, widely referenced resources with stable
          identifiers and own endpoints.
        - Prefer canonical/base types over views/variants (but keep exact names as given).
        - If uncertain, keep the original relative order.
        """
    ).strip("\n"),
    sql_ranking_signals=(
        "Keep both management and ITSM core entities in the high-confidence bucket. "
        "Within that bucket, put management entities first: identities/accounts, groups, "
        "organizations, roles/entitlements, and memberships/assignments. Then rank ITSM "
        "work items and containers: tickets/cases/incidents/problems/changes, work "
        "orders/work packages, queues/projects, and workflows/statuses. Within each "
        "domain, prefer canonical, first-class, broadly referenced tables over technical, "
        "partition, helper, audit, scheduler, or per-resource storage tables."
    ),
)

_PROFILES: Mapping[GenerationIntent, ObjectClassIntentProfile] = {
    GenerationIntent.MANAGEMENT: _MANAGEMENT_PROFILE,
    GenerationIntent.ITSM: _ITSM_PROFILE,
    GenerationIntent.MANAGEMENT_ITSM: _MANAGEMENT_ITSM_PROFILE,
}


def get_intent_profile(intent: GenerationIntent) -> ObjectClassIntentProfile:
    """Return the object-class vocabulary profile for the given intent."""
    return _PROFILES[intent]
