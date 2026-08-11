# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
REST relation prompts, one pair per pipeline stage.

The stages exist because judgement and observation need different amounts of context. A
single documentation chunk can show one class mentioning another without containing enough
information to decide whether that is an association, which side is the subject, or what the
inverse attribute is called. So the per-chunk stages only report evidence, and the pair stages
- which see every observation for a class pair at once - decide.

All prompts share :data:`RELATION_ONTOLOGY`; only the evidence rules below are REST-specific.
Literal braces are doubled because these strings are LangChain templates.
"""

import textwrap

from src.modules.digester.prompts.relation_ontology import RELATION_ONTOLOGY

REST_EVIDENCE_RULES = textwrap.dedent(
    """
<evidence_rules>
The documentation is REST/HTTP: OpenAPI or Swagger fragments, endpoint reference pages,
request/response examples and narrative prose. Links show up as, in rough order of strength:

1. Schema references
   - `$ref` to another schema, `allOf`/`oneOf` composition, `items.$ref` inside an array.
   - A property whose declared target type, reference metadata or documented value identifies
     another object class.
   - A property whose declared type or example value is another class object.
2. Relationship properties
   - Properties whose descriptions or schema constraints explicitly say that their values
     identify, contain, assign, own, grant, scope, or otherwise relate instances of another
     independently managed class.
3. Link objects
   - A third independently managed schema whose documented properties reference the two ends
     and may carry additional association-specific data. Its name is not evidence.
4. Endpoint surfaces
   - A sub-resource path structurally nesting one documented class below an instance of another.
   - Writes that add or remove references between instances of two documented classes.
   - Query parameters documented as filtering one class by an identifier of another.
5. Narrative prose
   - Any explicit natural-language statement that instances of one documented class receive,
     contain, grant, own, scope, or reference instances of another.

The exact identifier spelling is never evidence. Use descriptions, types and documented
behavior even when every class and attribute name is opaque.
</evidence_rules>
"""
)


# --- Stage 1: per-chunk evidence harvest ---

get_relation_harvest_system_prompt = (
    RELATION_ONTOLOGY
    + REST_EVIDENCE_RULES
    + textwrap.dedent(
        """
<task>
You are reading ONE fragment of a larger documentation set. Report every link between object
classes that this fragment supports. You are gathering evidence, not deciding anything.

REPORT GENEROUSLY
- Include links you are unsure about. A later step sees every fragment's findings for the same
  pair together and decides; a link you omit here can never be recovered.
- Report a link even when you can only see one side of it. Fragments are split by size, so the
  other side is usually in a different fragment.
- Report the same link once per distinct piece of evidence in this fragment. Repetition across
  fragments is expected and handled later.

STAY LITERAL
- Copy class names and attribute names exactly as the fragment writes them. Do not translate,
  singularize, or invent a name so a link looks complete - leave the attribute empty instead.
- `quote` must be a verbatim excerpt from the fragment, at most about 200 characters.
- `sourceClass` is the class the evidence hangs off; `targetClass` is the one it points at.
  Direction here is observational, not a subject/object decision - that is decided later.

WHAT NOT TO REPORT
- Links where both names refer to the same class.
- Transport wrappers, pagination envelopes, error payloads.
- Authentication, session or token references.
- A pair supported by nothing but similar names.

Report a link whose target is an embedded structure or a superclass as well, and set
`evidenceKind` accordingly - those are classified and rejected later, and recording them stops
them being re-proposed.

Return an empty list when the fragment shows no links.
</task>
"""
    )
)

get_relation_harvest_user_prompt = textwrap.dedent(
    """
Object classes already extracted from this documentation set (use these names when a mention
matches one; `embedded` and `abstract` mark classes that cannot be a relation end on their own):

<object_classes>
{object_classes}
</object_classes>

Summary of this fragment:

<summary>
{summary}
</summary>

Tags of this fragment:

<tags>
{tags}
</tags>

Documentation fragment:

<chunk>
{chunk}
</chunk>

Report every class-to-class link this fragment supports.
"""
)


# --- Stage 3: per-class sweep ---

get_relation_class_sweep_system_prompt = (
    RELATION_ONTOLOGY
    + REST_EVIDENCE_RULES
    + textwrap.dedent(
        """
<task>
You are given all documentation collected for ONE object class. Answer a single question:
which other object classes does this class relate to?

This view is wider than a single fragment, so use it to complete links a fragment-by-fragment
pass would only half-see:
- Attributes of the focus class that point at another class.
- Attributes on other classes that point back at the focus class, when this documentation
  mentions them.
- Endpoints under the focus class that expose another class.
- Prose describing membership, ownership, assignment or hierarchy.

Rules:
- Every observation must involve the focus class on one side.
- Copy attribute names verbatim; leave a side empty when the documentation does not name it.
- `quote` must be a verbatim excerpt, at most about 200 characters.
- Include self-links when the class genuinely references itself, with
  the focus class on both sides.
- Do not report links to embedded structures of the focus class as associations; report them
  with `evidenceKind` set to `schema_property` and say so in `note`.

Return an empty list when this class relates to nothing.
</task>
"""
    )
)

get_relation_class_sweep_user_prompt = textwrap.dedent(
    """
Focus object class:

<focus_class>
{focus_class}
</focus_class>

<focus_description>
{focus_description}
</focus_description>

Other object classes extracted from this documentation set:

<object_classes>
{object_classes}
</object_classes>

Documentation collected for the focus class:

<documentation>
{documentation}
</documentation>

Which other object classes does {focus_class} relate to?
"""
)


# --- Stage 4: focused re-read of one weak pair ---

get_relation_pair_focus_system_prompt = (
    RELATION_ONTOLOGY
    + REST_EVIDENCE_RULES
    + textwrap.dedent(
        """
<task>
Earlier stages found thin or one-sided evidence for ONE specific class pair. You are given the
documentation where both classes appear. Answer only about this pair.

Look specifically for what is still missing:
- The attribute on each side that carries the reference, named verbatim.
- Whether either attribute holds one value or many.
- Whether a third class sits between the two and carries the association.
- Whether the link exists only as an endpoint path rather than a schema property.

If the documentation shows the pair is NOT associated - the names merely look similar, one is
an embedded structure of the other, or one extends the other - return an observation with
`evidenceKind` set to `embedded_metadata` or `inheritance_metadata` and explain in `note`.

Copy every name verbatim. Return an empty list only when the documentation says nothing about
either class.
</task>
"""
    )
)

get_relation_pair_focus_user_prompt = textwrap.dedent(
    """
Class pair under investigation:

<pair>
{class_a} and {class_b}
</pair>

Descriptions and structural metadata for both classes. Use these to interpret opaque class
names; storage-only relevance identifiers are intentionally absent:

<class_metadata>
{class_metadata}
</class_metadata>

What earlier stages already observed about this pair:

<known_observations>
{known_observations}
</known_observations>

Documentation where these classes appear:

<documentation>
{documentation}
</documentation>

Complete the picture for this pair.
"""
)


# --- Stage 5: adjudication ---

get_relation_adjudication_system_prompt = RELATION_ONTOLOGY + textwrap.dedent(
    """
<task>
You are given every observation the pipeline collected for ONE class pair, from several
independent passes over the documentation. Decide which associations, if any, this pair holds.

FIRST decide whether the pair is associated at all. Return an empty `relations` list and set
`rejectionKind` when:
- one class is an embedded structure of the other (`embedded`),
- one class extends the other (`inheritance`),
- either side's documented purpose is transport-only, session-oriented or audit-only, or the
  only support is name similarity (`not_a_relation`).

OTHERWISE emit one entry per DISTINCT association. Most pairs hold exactly one. Emit more only
when different attributes carry genuinely different roles:
- Two different documented attributes with different relationship meanings are two relations.
- Two inverse descriptions or navigation directions of the same documented association are one
  relation, with both attribute names filled in when available.
Never split one association across two entries because the documentation described it twice.

For each association:
1. Kind. `link_object` when a third class carries it - name that class in `linkObjectClass`.
   `virtual_endpoint` when only an endpoint path supports it. `inverse_reference` when only the
   object side has a documented attribute. Otherwise `reference`. Set `isRelation` true.
2. Orientation. Determine the subject and object exclusively from each class's supplied
   description, structural metadata and observed behavior. The subject consumes or receives
   the documented access; the object grants, defines or scopes it. Names are opaque and no
   vocabulary-based fallback is allowed. Explain the evidence in `rationale`.
3. Attribute names, using ONLY names that appear in the observations or in the known attribute
   lists. Leave a side empty rather than inventing a name.
4. Cardinality per side when the evidence says so, otherwise null.
5. Confidence:
   - `high`   a declared schema property or explicit reference, corroborated by more than one
              observation or naming both sides.
   - `medium` a single clear piece of schema or endpoint evidence.
   - `low`    prose only, or a single weak mention.

`subject` and `object` must both be one of the two class names given in the task, spelled
exactly as provided. `name` is lowercase snake_case, default `{{subject}}_to_{{object}}`; when
the pair holds several associations, suffix each with the documented attribute that distinguishes
it.
</task>
"""
)

get_relation_adjudication_user_prompt = textwrap.dedent(
    """
Class pair to judge (use these exact spellings in `subject` and `object`):

<class_a>
{class_a}
</class_a>

<class_b>
{class_b}
</class_b>

Structural metadata for both classes:

<class_metadata>
{class_metadata}
</class_metadata>

Attribute names actually extracted for these classes. An attribute you name should appear here
unless the observations show it verbatim:

<known_attributes>
{known_attributes}
</known_attributes>

Everything observed about this pair:

<observations>
{observations}
</observations>

Which associations, if any, does this pair hold?
"""
)


# --- Stage 6: adversarial verification ---

get_relation_verification_system_prompt = RELATION_ONTOLOGY + textwrap.dedent(
    """
<task>
A relation has been proposed. Your job is to REFUTE it, not to confirm it. Assume it is wrong
until the evidence forces you to accept it.

Set `refuted` to true when any of these holds:
- The evidence supports no link at all, only that both classes are mentioned nearby.
- The claimed relation is really an embedded structure of one class, or schema inheritance.
- Either side's documented purpose is transport-only, session-oriented or audit-only rather
  than an independently managed object class.
- The subject and object are inverted: the object is clearly the access consumer.
- An attribute name in the proposal appears nowhere in the evidence or the known attribute
  lists, and no documented alternative exists.

When an attribute name is wrong but a correct one is visible in the evidence, keep `refuted`
false and put the correct name in the matching `corrected...` field.

Default to `refuted` true when you cannot point at concrete evidence. A relation dropped here
costs one missing suggestion; a wrong one costs a connector that does not work.
</task>
"""
)

get_relation_verification_user_prompt = textwrap.dedent(
    """
Proposed relation:

<relation>
{relation}
</relation>

Descriptions and structural metadata for both classes. Interpret the proposed orientation from
these facts, never from identifier spelling:

<class_metadata>
{class_metadata}
</class_metadata>

Evidence it was built from:

<observations>
{observations}
</observations>

Attribute names actually extracted for the two classes:

<known_attributes>
{known_attributes}
</known_attributes>

Try to refute this relation.
"""
)
