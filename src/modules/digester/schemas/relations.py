# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Optional

from pydantic import BaseModel, Field

from src.common.schema import CamelCaseModel

# --- Relation ---


class RelationRecord(CamelCaseModel):
    """
    Relationship between two object classes discovered in the schema.
    """

    name: str = Field(
        ...,
        description=(
            "Stable machine-friendly relation identifier in lowercase snake_case "
            "(e.g., 'user_to_group', 'membership_to_project')."
        ),
    )
    display_name: str = Field(
        ...,
        description=(
            "Human-readable relation name shown to users based on documentation "
            "(e.g., 'User to Group', 'Membership to Project')."
        ),
    )
    short_description: str = Field(
        default="",
        description=(
            "One concise sentence describing the relation meaning, grounded in documentation evidence. "
            "Leave empty when no trustworthy short description can be derived."
        ),
    )
    subject: str = Field(
        ...,
        description=(
            "Normalized subject object-class name (lowercase) selected from the relevant object classes list. "
            "The subject is the side that consumes/receives membership, entitlement, assignment, or access."
        ),
    )
    subject_attribute: Optional[str] = Field(
        default="",
        description=(
            "Attribute on the subject that points to object identifiers/references (e.g., groups, roles, projects). "
            "Can be a virtual attribute name when the relation is explicit only via inverse/query evidence."
        ),
    )
    object: str = Field(
        ...,
        description=(
            "Normalized object object-class name (lowercase) selected from the relevant object classes list. "
            "The object is the target entity referenced/assigned/owned by the subject."
        ),
    )
    object_attribute: Optional[str] = Field(
        default="",
        description=(
            "Inverse attribute on the object that points back to subject identifiers/references "
            "(e.g., members, owners). Leave empty when not documented or not applicable."
        ),
    )


class RelationsResponse(BaseModel):
    """
    Container for relation records extracted from a schema fragment. Return an empty list when none qualify.
    """

    relations: List[RelationRecord] = Field(
        default_factory=list,
        description="List of discovered relations with normalized class names and supporting fields.",
    )


# --- End Relation ---
