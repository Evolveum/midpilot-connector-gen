# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
Intermediate models for staged relation detection.

These models are the working state of the relation pipeline, not part of the
midPoint-facing contract. They are persisted under ``relationsAnalysisOutput`` so a
reviewer can see why a relation was accepted or rejected, and so later stages
(codegen) can reuse the per-side detail; the API response is still projected down to
``RelationsResponse``.
"""

from typing import Annotated, Any, List, Literal, Optional, get_args

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from src.core.schema import CamelCaseModel
from src.modules.digester.enums import ConfidenceLevel
from src.modules.digester.schemas.common import RelevantDocumentationsMixin
from src.shared.enums import ApiType

# --- Vocabulary ---

RelationKind = Literal[
    "reference",
    "inverse_reference",
    "link_object",
    "virtual_endpoint",
    "embedded",
    "inheritance",
    "not_a_relation",
]
"""How an observed class-to-class link is classified.

The first four are relations that midPoint can model as a ConnId association; the last
three are explicit rejections that are recorded rather than silently dropped, so a
reviewer confirms them once instead of seeing them re-proposed on every run.
"""

RELATION_KINDS_ACCEPTED: frozenset[str] = frozenset(
    {"reference", "inverse_reference", "link_object", "virtual_endpoint"}
)
"""Kinds that become a relation record in the API response."""

RejectionKind = Literal["embedded", "inheritance", "not_a_relation"]
"""Valid reasons for rejecting a class pair during adjudication."""

EvidenceKind = Literal[
    "schema_property",
    "schema_reference",
    "endpoint_path",
    "narrative",
    "attribute_metadata",
    "inheritance_metadata",
    "embedded_metadata",
    "scim_reference",
    "scim_mapping",
    "sql_foreign_key",
    "sql_junction_table",
]
"""What kind of documentation evidence an observation rests on."""

DETERMINISTIC_EVIDENCE_KINDS: frozenset[str] = frozenset({"attribute_metadata", "endpoint_path", "schema_reference"})
"""Evidence read off an already-validated schema rather than interpreted from prose.

Every stage that has to rank or pick among observations prefers these, so the preference is
stated once here instead of being restated as a literal set at each use.
"""

NON_REFERENCE_EVIDENCE_KINDS: frozenset[str] = frozenset({"embedded_metadata", "inheritance_metadata"})
"""Evidence recorded so it can be rejected explicitly, never a pointer at another object class.

An embedded structure is a complex attribute of its own class and inheritance is a schema
relationship, so neither can carry an association between two classes.
"""

EvidenceSource = Literal[
    "chunk_harvest",
    "class_sweep",
    "pair_focus",
    "attribute_schema",
    "endpoint_schema",
    "object_class_metadata",
    "link_object_expansion",
]
"""Which pipeline stage produced an observation."""

AttributeSchemaState = Literal["missing", "invalid", "empty", "available"]
"""Availability of one class's extracted attribute schema during grounding."""


def _coerce_to_vocabulary(allowed: tuple[str, ...], fallback: str):
    """Map an out-of-vocabulary LLM value onto the nearest safe member of a Literal.

    A strict Literal would raise, and a raised ValidationError discards the whole response -
    every other observation in that chunk with it. Recall matters more than the exact label
    on one field, so an unrecognized value degrades instead of destroying the batch.
    """

    def _coerce(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        return normalized if normalized in allowed else fallback

    return _coerce


ToleratedEvidenceKind = Annotated[
    EvidenceKind,
    BeforeValidator(_coerce_to_vocabulary(get_args(EvidenceKind), "narrative")),
]
ToleratedRelationKind = Annotated[
    RelationKind,
    BeforeValidator(_coerce_to_vocabulary(get_args(RelationKind), "reference")),
]
ToleratedRejectionKind = Annotated[
    RejectionKind,
    BeforeValidator(_coerce_to_vocabulary(get_args(RejectionKind), "not_a_relation")),
]
ToleratedConfidence = Annotated[
    ConfidenceLevel,
    BeforeValidator(_coerce_to_vocabulary(tuple(level.value for level in ConfidenceLevel), ConfidenceLevel.LOW.value)),
]


class ScimRelationEvidence(CamelCaseModel):
    """SCIM wire-level evidence kept beside a logical relation observation."""

    application_attribute: str = Field(
        default="",
        description="Exact application/ConnId attribute name, which may intentionally differ from SCIM.",
    )
    scim_path: str = Field(
        default="",
        description=(
            "Exact SCIM schema path or sub-attribute path documented on the wire, such as "
            "groups.$ref. The public relation attribute remains the parent logical attribute "
            "(groups), never the leaf $ref/value sub-attribute."
        ),
    )
    reference_types: List[str] = Field(
        default_factory=list,
        description="Documented SCIM referenceTypes values, copied without normalization.",
    )
    vendor_deviation: str = Field(
        default="",
        description="Concise description of a documented vendor-specific mapping or behavior.",
    )


class SqlRelationEvidence(CamelCaseModel):
    """Physical SQL binding kept beside a logical relation observation."""

    logical_attribute: str = Field(
        default="",
        description="Logical connector attribute name when it differs from the database column.",
    )
    source_table: str = Field(default="", description="Exact physical source table or view name.")
    source_columns: List[str] = Field(
        default_factory=list,
        description="Ordered physical source columns, including every column of a composite key.",
    )
    target_table: str = Field(default="", description="Exact physical target table or view name.")
    target_columns: List[str] = Field(
        default_factory=list,
        description="Ordered referenced columns corresponding to sourceColumns.",
    )
    constraint_name: str = Field(default="", description="Exact FOREIGN KEY constraint name when documented.")
    junction_table: str = Field(
        default="",
        description="Exact association/junction table name when a third table carries the link.",
    )


class RelationObservation(CamelCaseModel):
    """
    One raw observation that a class appears to point at another class.

    Deliberately unjudged: the extraction stages record what the documentation says and
    leave acceptance to the later adjudication stage, which sees every observation for
    the same class pair at once.
    """

    source_class: str = Field(
        ...,
        description="Class that holds the pointer, exactly as written in the documentation.",
    )
    target_class: str = Field(
        ...,
        description="Class being pointed at, exactly as written in the documentation.",
    )
    source_attribute: str = Field(
        default="",
        description=(
            "Attribute on the source class that carries the reference, copied verbatim from the "
            "documentation. Empty when the evidence does not name one; never infer it from an identifier "
            "pattern or invent a name."
        ),
    )
    target_attribute: str = Field(
        default="",
        description=(
            "Attribute on the target class that points back, copied verbatim from the documentation. "
            "Empty when the evidence does not name one; never invent a name."
        ),
    )
    multi_valued: Optional[bool] = Field(
        default=None,
        description=(
            "True when the referencing attribute is an array/list/multi-valued property, false when it holds "
            "a single reference, null when the documentation does not say."
        ),
    )
    evidence_kind: ToleratedEvidenceKind = Field(
        default="narrative",
        description=(
            "What the observation rests on: 'schema_property' for a declared property, 'schema_reference' for "
            "a $ref or explicit type reference, 'endpoint_path' for a sub-resource path, 'scim_reference' or "
            "'scim_mapping' for SCIM metadata/vendor mappings, 'sql_foreign_key' or 'sql_junction_table' for "
            "SQL constraints, and 'narrative' for prose."
        ),
    )
    quote: str = Field(
        default="",
        description=(
            "Short verbatim excerpt from the fragment that supports this observation (at most ~200 characters). "
            "Must be copied from the text, not paraphrased."
        ),
    )
    note: str = Field(
        default="",
        description="Optional one-line remark, e.g. that a third class links the two.",
    )
    via_class: str = Field(
        default="",
        description=(
            "Name of a third class that carries this link, when the two ends are only connected through "
            "it. System-populated: leave this empty, the pipeline fills it when it derives an observation "
            "from an association class."
        ),
    )
    scim_evidence: Optional[ScimRelationEvidence] = Field(
        default=None,
        description=(
            "SCIM application-to-wire mapping supporting this observation. Populate only for SCIM evidence; "
            "preserve custom casing and vendor-specific names."
        ),
    )
    sql_evidence: Optional[SqlRelationEvidence] = Field(
        default=None,
        description=(
            "Physical table/column/constraint binding supporting this observation. Populate only for SQL "
            "evidence and preserve composite-column order."
        ),
    )


class RelationObservationsResponse(BaseModel):
    """
    Container for observations found in one documentation fragment or for one class.

    Return an empty list when the fragment says nothing about links between classes.
    """

    observations: List[RelationObservation] = Field(
        default_factory=list,
        description="Every class-to-class link the fragment supports, including ones you are unsure about.",
    )


class RelationVerdict(CamelCaseModel):
    """
    Decision for one class pair, made with every observation for that pair in view.
    """

    is_relation: bool = Field(
        ...,
        description="True when this pair is a real association between two manageable object classes.",
    )
    kind: ToleratedRelationKind = Field(
        default="reference",
        description=(
            "Classification. 'reference': the subject holds the pointer. 'inverse_reference': only the object "
            "holds it. 'link_object': a third class carries the association. 'virtual_endpoint': the link exists "
            "only as an API path. 'embedded': the target is a complex attribute, not a separate resource. "
            "'inheritance': one class extends the other. 'not_a_relation': anything else."
        ),
    )
    subject: str = Field(
        default="",
        description=(
            "Object class whose documented purpose and behavior show that its instances consume or receive "
            "the association's access. Use one of the two supplied class names exactly; the name itself is "
            "opaque and must not determine the role. Empty when isRelation is false."
        ),
    )
    subject_attribute: str = Field(
        default="",
        description=(
            "Attribute on the subject listing the object references. Use a name that appears in the evidence; "
            "leave empty when no documented name exists."
        ),
    )
    subject_multi_valued: Optional[bool] = Field(
        default=None,
        description="Whether the subject-side attribute holds multiple values. Null when unknown.",
    )
    object: str = Field(
        default="",
        description=(
            "Object class whose documented purpose and behavior show that its instances grant, define or scope "
            "the association's access. Use one of the two supplied class names exactly; the name itself is "
            "opaque and must not determine the role. Empty when isRelation is false."
        ),
    )
    object_attribute: str = Field(
        default="",
        description=(
            "Inverse attribute on the object listing subject references. Use a name that appears in the "
            "evidence; leave empty when no documented name exists."
        ),
    )
    object_multi_valued: Optional[bool] = Field(
        default=None,
        description="Whether the object-side attribute holds multiple values. Null when unknown.",
    )
    link_object_class: str = Field(
        default="",
        description=(
            "Exact name of the independently managed third class whose documented structure carries the "
            "association when kind is 'link_object'. Do not infer this role from its name. Empty otherwise."
        ),
    )
    name: str = Field(
        default="",
        description="Stable lowercase snake_case identifier, default pattern '{subject}_to_{object}'.",
    )
    display_name: str = Field(
        default="",
        description="Human-readable title grounded in the documented meaning of the association.",
    )
    short_description: str = Field(
        default="",
        description="One sentence describing the association, grounded in the evidence. Empty when unclear.",
    )
    confidence: ToleratedConfidence = Field(
        default=ConfidenceLevel.LOW,
        description="How well the evidence supports this decision: low, medium or high.",
    )
    rationale: str = Field(
        default="",
        description="One or two sentences explaining the decision, naming the evidence that drove it.",
    )


class RelationPairJudgement(BaseModel):
    """
    Adjudication result for one class pair.

    A pair can carry more than one association when different documented attributes express
    different roles, so the stage that sees the whole pair decides how many relations it holds.
    """

    relations: List[RelationVerdict] = Field(
        default_factory=list,
        description=(
            "One entry per DISTINCT association between the two classes. Two classes are often connected in "
            "several ways at once - one class can both contain instances of the other and record which of them "
            "are responsible for it - and each way is its own entry, whether the evidence for it is an "
            "attribute, an endpoint surface or prose. Emit one entry only when the evidence describes a single "
            "way, restated or seen from both ends. Give each entry a name, displayName and shortDescription "
            "that say which way it is, so two entries are never interchangeable. Empty when the pair carries "
            "no association at all."
        ),
    )
    rejection_kind: ToleratedRejectionKind = Field(
        default="not_a_relation",
        description=(
            "Why the pair carries no association, used only when `relations` is empty: 'embedded' when one "
            "class is a complex attribute of the other, 'inheritance' when one extends the other, otherwise "
            "'not_a_relation'."
        ),
    )
    rationale: str = Field(
        default="",
        description="One or two sentences explaining the decision for the pair, naming the evidence.",
    )


class RelationRefutation(CamelCaseModel):
    """
    Adversarial second opinion on an accepted relation.
    """

    refuted: bool = Field(
        ...,
        description=(
            "True when the evidence does not actually support the underlying relation. An incorrect attribute "
            "representation on an otherwise supported relation is a correction, not by itself a refutation. "
            "Default to true when the evidence is only a name similarity or an unrelated mention."
        ),
    )
    relation_supported_after_correction: bool = Field(
        default=False,
        description=(
            "True only when the association itself is supported and the proposed relation becomes correct after "
            "applying correctedSubjectAttribute and/or correctedObjectAttribute. This lets verification repair a "
            "nested SCIM path such as groups.$ref -> groups without discarding the real association."
        ),
    )
    reason: str = Field(
        default="",
        description="One sentence stating what the evidence does or does not show.",
    )
    corrected_subject_attribute: str = Field(
        default="",
        description=(
            "Replacement subject-side attribute name when the claimed one does not appear in the evidence but "
            "another one clearly does. Empty when no correction is needed."
        ),
    )
    corrected_object_attribute: str = Field(
        default="",
        description=(
            "Replacement object-side attribute name when the claimed one does not appear in the evidence but "
            "another one clearly does. Empty when no correction is needed."
        ),
    )

    @model_validator(mode="after")
    def validate_supported_correction(self) -> "RelationRefutation":
        """A correction-only outcome must identify what is safe to repair."""
        if self.relation_supported_after_correction and not (
            self.corrected_subject_attribute.strip() or self.corrected_object_attribute.strip()
        ):
            raise ValueError(
                "relationSupportedAfterCorrection requires correctedSubjectAttribute or correctedObjectAttribute"
            )
        if self.relation_supported_after_correction:
            # Keep the persisted audit state coherent even if the LLM marked the malformed
            # original fields as refuted while also saying the underlying relation survives.
            self.refuted = False
        return self


class RelationDecision(CamelCaseModel):
    """
    One candidate association and what happened to it after adjudication.
    """

    verdict: RelationVerdict = Field(..., description="The adjudicated association.")
    accepted: bool = Field(default=False, description="Whether it reached the API response.")
    rejection_reason: str = Field(default="", description="Why it did not, when accepted is false.")
    refutation: Optional[RelationRefutation] = Field(
        default=None,
        description="Verification result. Absent when verification is disabled or never ran.",
    )
    ungrounded_attributes: List[str] = Field(
        default_factory=list,
        description=(
            "Attribute names the verdict claimed that appear neither in the class's extracted attributes nor "
            "in the cited evidence. They are cleared on the emitted record rather than kept."
        ),
    )
    attribute_schema_states: dict[str, AttributeSchemaState] = Field(
        default_factory=dict,
        description=(
            "Per-class attribute-schema availability used while grounding this decision: "
            "missing, invalid, empty or available."
        ),
    )


class RelationPairAnalysis(CamelCaseModel, RelevantDocumentationsMixin):
    """
    Everything the pipeline learned about one unordered class pair.
    """

    pair_key: str = Field(..., description="Stable identifier of the class pair, '<classA>|<classB>' sorted.")
    class_a: str = Field(..., description="First class of the pair in sorted order.")
    class_b: str = Field(..., description="Second class of the pair in sorted order.")
    accepted: bool = Field(
        default=False,
        description="Whether at least one association from this pair reached the API response.",
    )
    rejection_reason: str = Field(
        default="",
        description="Why the pair carries no association, when accepted is false.",
    )
    observation_sources: List[EvidenceSource] = Field(
        default_factory=list,
        description="Distinct pipeline stages that contributed observations for this pair.",
    )
    observations: List[RelationObservation] = Field(
        default_factory=list,
        description=(
            "Observations collected for this pair across all stages. The persisted copy may be capped by the "
            "configured per-pair storage limit after adjudication has used the full set."
        ),
    )
    decisions: List[RelationDecision] = Field(
        default_factory=list,
        description="One entry per candidate association the adjudication stage found for this pair.",
    )


class RelationAnalysisStats(CamelCaseModel):
    """Per-stage counters, so a thin result can be traced back to the stage that thinned it."""

    chunks_harvested: int = 0
    classes_swept: int = 0
    pairs_refocused: int = 0
    link_object_pairs_expanded: int = 0
    pairs_adjudicated: int = 0
    relations_verified: int = 0
    observations_total: int = 0
    observations_deterministic: int = 0
    relations_emitted: int = 0


class RelationsAnalysis(CamelCaseModel):
    """
    Persisted working state of one relation extraction run.
    """

    job_id: str = Field(default="", description="Job that produced this analysis.")
    api_type: Optional[ApiType] = Field(
        default=None,
        description="Protocol profile used by every LLM stage in this analysis run.",
    )
    output_fingerprint: str = Field(
        default="",
        description="SHA-256 identity of the exact relationsOutput this analysis describes.",
    )
    stats: RelationAnalysisStats = Field(default_factory=RelationAnalysisStats)
    pairs: List[RelationPairAnalysis] = Field(
        default_factory=list,
        description="All analyzed pairs, accepted and rejected alike.",
    )
