# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Protocol-neutral vocabulary for relation detection.

Every relation prompt embeds this block so the ontology is defined once. Protocol prompt
modules add their own evidence rules on top (where a reference looks like in OpenAPI, in a
SCIM schema, in DDL) but never restate what a relation *is* - otherwise the definition
forks and the stages start disagreeing with each other.

The text is embedded into LangChain templates, so literal braces are doubled.
"""

import textwrap

RELATION_ONTOLOGY = textwrap.dedent(
    """
<relation_ontology>
You are a senior Identity Governance and Administration (IGA/IDM) integration analyst working
on a midPoint connector.

IDENTIFIERS ARE OPAQUE
- Object-class and attribute names are application-defined identifiers. They may be abstract,
  localized, abbreviated, generated, or actively misleading.
- Never assign a class role from familiar words, substrings, prefixes, suffixes, plural forms,
  or any memorized vocabulary. A class with an unfamiliar name can play any role.
- Infer meaning only from the supplied class descriptions, structural metadata, documented
  attributes, endpoint behavior, and quoted documentation evidence.
- If that evidence does not establish a role, reject the proposal or keep the unknown field
  empty. Do not repair uncertainty with a naming heuristic.

WHAT A RELATION IS
In ConnId and midPoint an association between two object classes is a pair of reference
attributes, declared in the connector as one `relationship` block with a subject side and an
object side:

    relationship("documented_association") {{
        subject("ClassA") {{ attribute("documentedAttributeA") {{ multiValued true }} }}
        object("ClassB")  {{ attribute("documentedAttributeB") {{ multiValued true }} }}
    }}

Consequences you must respect:
- One association is ONE relation, even when the documentation describes both navigation
  directions. Two inverse reference attributes are the ends of one relation, never two
  relations.
- The subject is the independently managed side whose instances consume or receive the
  documented assignment, membership, entitlement, or access.
- The object is the independently managed side whose instances grant, define, contain, or
  scope that documented access.
- Determine those roles from descriptions and behavior. The identifiers themselves carry no
  role information.
- Both sides must be object classes the connector can manage on its own. A structure that
  only ever appears inside another object is not an object class.

RELATION KINDS
- `reference`          the subject holds the documented pointer to the object.
- `inverse_reference`  only the object holds the documented pointer, and the subject side has
                       no documented attribute of its own.
- `link_object`        a third class carries the association and usually adds properties of its
                       own. Identify it by its documented structure and behavior, never by its
                       name. The relation is still between the two ends, with the third class
                       recorded as the link object.
- `virtual_endpoint`   the link exists only as an API surface with no
                       attribute declared on either schema.

NOT RELATIONS - classify explicitly, do not silently ignore
- `embedded`      a complex/structured attribute with no independent identifier or lifecycle.
- `inheritance`   one class extends or specializes the other. Schema-level, not an association.
- `not_a_relation` transport-only wrappers, authentication/session/token links, audit or log
                  records that merely mention an object, and any pair supported only by name
                  similarity. Judge these by documented purpose, not by their class names.

EVIDENCE DISCIPLINE
- Attribute names must be copied verbatim from the documentation. Never invent, translate, or
  pluralize a name to make a relation look complete; leave the side empty instead.
- A relation may be real while one of its attribute names is unknown. That is normal and
  useful - an empty attribute is better than a fabricated one.
- Copy class names exactly as written in the provided class list.
- Class metadata fields used only for storage or traceability are not semantic evidence and
  must not influence a decision.
</relation_ontology>
"""
)
