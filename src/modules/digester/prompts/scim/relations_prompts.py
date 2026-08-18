# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""SCIM-specific system prompts for the staged relation pipeline."""

import textwrap

from src.modules.digester.prompts.relation_ontology import RELATION_ONTOLOGY

SCIM_EVIDENCE_RULES = textwrap.dedent(
    """
<scim_evidence_rules>
The documentation describes a SCIM integration. Treat RFC-standard names as useful context,
never as a substitute for the product documentation. Vendors commonly rename, omit, extend,
or reinterpret SCIM attributes.

Strong evidence includes:
- SCIM attribute metadata such as `type: reference`, `referenceTypes`, `multiValued`, and a
  complex value containing `value` or `$ref` sub-attributes.
- ResourceType declarations and schema documentation that map one independently manageable
  resource to another.
- Explicit vendor mappings between an application/ConnId attribute and its SCIM wire path.
  Preserve both spellings in `scimEvidence`. For example, an application attribute documented
  as `Username` can map to the standard SCIM path `userName`; the relation attribute and
  `applicationAttribute` remain `Username`, while `scimPath` is `userName`. Never silently
  normalize one into the other.
- PATCH examples, filters, and narrative text that explicitly add, remove, list, assign, or
  reference instances of another resource type.

Model every SCIM reference at two levels:
- `sourceAttribute`/`targetAttribute` is the logical application/ConnId carrier attribute.
- `scimEvidence.scimPath` is the complete wire path to the reference sub-attribute.
For example, use `groups` plus `groups.$ref`, `members` plus `members.$ref`, and `manager`
plus `manager.$ref`. Never emit bare `$ref` or `value` as a relation attribute. Cardinality
belongs to the carrier (`groups`/`members`), not to its scalar `$ref`/`value` child.

Before treating a class as a relation endpoint, classify what the documentation describes:
- An independently manageable ResourceType has its own resource identity and management
  surface (for example a ResourceType endpoint/declaration or equivalent vendor resource API).
- A schema extension augments a ResourceType; merge its attributes into that resource when
  reasoning about relations. It is not a separate object-class endpoint.
- A complex attribute/sub-attribute is embedded in its owning resource.
- A schema definition, attribute definition, connector mapping object, shadow representation,
  schema URI, or `objectClass` discriminator is metadata, not an instance-level relation.
Being listed in the supplied extracted object classes is not proof of being a ResourceType.

Do not infer a relation from a familiar SCIM name alone. `groups`, `members`, `manager`, and
other standard-looking names can be customized. Conversely, opaque vendor names can carry a
real relation when their type, mapping, example, or description proves it.

`schemaExtensions` composes schemas onto one resource and is not by itself a relation between
manageable resources. A complex embedded value is not a relation unless the documentation
shows that it references an independently manageable resource. SCIM has no REST-style
sub-resource relation merely because two names occur in a URL.

SCIM core semantics are evidence when the corresponding schema is supplied: `User.groups`
references Group resources, `Group.members` references User resources and MAY reference Group
resources for nested groups, and the enterprise extension's `manager` belongs to User and
references another User. Generic SCIM support alone does not prove that a provider implements
nested groups; use at most medium confidence unless provider evidence confirms that behavior.

Core `User.roles` and `User.entitlements` are multi-valued complex values, not references to
standalone Role/Entitlement ResourceTypes by name alone. Accept those as vendor relations only
when documentation proves a separately manageable resource and explicitly maps the carrier's
`value`, id, or `$ref` to that resource. Describe such evidence as a vendor resource mapping,
not as an RFC-defined SCIM reference.
</scim_evidence_rules>
"""
)


def _system(task: str) -> str:
    return RELATION_ONTOLOGY + SCIM_EVIDENCE_RULES + textwrap.dedent(task)


get_relation_harvest_system_prompt = _system(
    """
<task>
Read one SCIM documentation fragment and report every supported link between extracted
resource classes. Gather evidence generously; a later pass decides whether it is a relation.

Copy resource names and application attribute names exactly. When the fragment distinguishes
an application attribute from its SCIM path, keep the application name in `sourceAttribute`
or `targetAttribute` and populate `scimEvidence` with both spellings. State `referenceTypes`
and vendor deviations there, and multi-valued behavior on the observation. Use a short verbatim
`quote`. Leave an unknown attribute empty instead of substituting a standard SCIM name.
When a reference is nested, place its parent carrier in the attribute field and its full path
in `scimEvidence.scimPath`; `groups.$ref` therefore produces `sourceAttribute=groups`, never
`sourceAttribute=$ref`.

Report self-references and possible embedded/inheritance cases so they can be rejected later.
Ignore transport wrappers, authentication/token objects, schema-extension composition by
itself, and pairs supported only by familiar SCIM vocabulary. Return an empty list when the
fragment contains no class-to-class evidence.
</task>
"""
)

get_relation_class_sweep_system_prompt = _system(
    """
<task>
Review all SCIM documentation collected for one focus resource. Find every independently
manageable resource it references or that references it. Reconcile schema metadata,
ResourceType declarations, PATCH/filter examples, prose, and explicit application-to-SCIM
attribute mappings.

Every observation must involve the focus class. Copy custom application attribute spelling
verbatim and put a different SCIM wire path in `scimEvidence`; never replace `Username` with `userName`
merely because the latter is standard. Report distinct attributes as distinct observations,
including self-links. Mark embedded complex values and schema composition clearly rather than
promoting them to associations. Return an empty list when no evidence exists.
Treat an extension attribute such as enterprise `manager.$ref` as an attribute of its owning
ResourceType (User), not as an EnterpriseUser-to-User relation.
</task>
"""
)

get_relation_pair_focus_system_prompt = _system(
    """
<task>
Re-read the SCIM documentation for one weak class pair. Recover missing per-side application
attribute names, corresponding SCIM paths, `referenceTypes`, cardinality, and evidence of
vendor customization. Check whether several differently named attributes express several
associations between the same classes.

Names are opaque. A standard-looking name is not proof, and a custom name is not a reason to
reject a link. Copy documented application names verbatim, retain different wire names in
`scimEvidence`, and never merge separate mappings. Report embedded/schema-extension evidence explicitly
when the pair is not a manageable-resource association. Return no observations only when the
documentation says nothing relevant about the pair.
Repair any bare `$ref`/`value` observation to its documented carrier while retaining the full
wire path in `scimEvidence`. Confirm that both ends are independently manageable ResourceTypes;
schema/meta objects and extension schemas cannot become ends or link objects.
</task>
"""
)

get_relation_adjudication_system_prompt = _system(
    """
<task>
Judge all observations for one SCIM resource pair. Reject embedded complex structures,
schema-extension composition, inheritance, transport-only resources, and name-only guesses.
Otherwise emit one verdict for each distinct documented association.

Choose subject/object from documented resource behavior, not the words User, Group, member,
or role. Use only application/ConnId attribute names present in known attributes or evidence.
If SCIM uses a different wire path, keep the application name in the verdict and cite the
wire mapping in the rationale; never normalize it away. An inverse description of the same
association is one verdict, while attributes with different meanings are separate verdicts.
Use the logical carrier (`groups`, `members`, `manager`, or the exact vendor attribute) in the
verdict. Never put bare `$ref` or `value` into `subjectAttribute`/`objectAttribute`; cite the
complete nested path from `scimEvidence` in the rationale instead.

Use `reference` or `inverse_reference` according to the documented carrier. Use `link_object`
only for an independently manageable third resource carrying the association. Do not use
`virtual_endpoint` for a SCIM link merely because HTTP examples are present. Leave unknown
attributes empty. Confidence must reflect explicit schema/reference metadata and independent
corroboration, not conformity to standard SCIM naming.
</task>
"""
)

get_relation_verification_system_prompt = _system(
    """
<task>
Try to refute one proposed SCIM association. Refute it when the evidence is only standard-name
familiarity, schema-extension composition, an embedded value, an unrelated resource mention,
or a fabricated attribute. Evaluate only the proposed association when the pair has several.

Do not reject a vendor-specific application name because it differs from the RFC spelling.
When documentation maps `Username` to `userName`, `Username` is grounded and must not be
corrected to `userName`. Correct an attribute only to another application/ConnId attribute
explicitly documented for this same association. Default to refuted when concrete evidence is
absent and explain the decisive SCIM or vendor-specific evidence in one sentence.

Distinguish an invalid association from an invalid attribute representation. If the underlying
association is supported but a verdict used `$ref`/`value` instead of its parent carrier, set
`relationSupportedAfterCorrection=true`, provide the corrected attribute(s), and do not discard
the association merely because the original field was wrong. Set that flag false when the
ResourceType-to-ResourceType association itself is unsupported. For example, User-to-Group with
`$ref` on both sides is corrected to `groups`/`members`; it is not refuted. EnterpriseUser-to-User
is refuted when EnterpriseUser is only the enterprise schema extension, while User-to-User via
`manager` remains valid.
</task>
"""
)
