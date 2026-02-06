# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import List, Literal

from pydantic import BaseModel, Field, field_validator

from ...config import config


class ChunkFilterCriteria(BaseModel):
    """
    Schema defining the criteria for filtering chunks.
    In a case of conflict between allowed and excluded categories/tags, exclusion takes precedence.
    """

    min_length: int | None = 0
    max_length: int | None = None
    min_endpoints_num: int | None = Field(default=0, description="Minimum number of endpoints required in a chunk")
    max_endpoints_num: int | None = Field(default=None, description="Maximum number of endpoints allowed in a chunk")
    allowed_categories: List[str] | None = Field(
        default=None,
        description="List of allowed categories for chunk filtering, if None, all categories that are not excluded are allowed",
    )
    excluded_categories: List[str] | None = Field(
        default=None,
        description="List of excluded categories for chunk filtering, in a case of conflict with allowed_categories, exclusion takes precedence",
    )
    allowed_tags: List[List[str]] | None = Field(
        default=None,
        description="""
        List of lists of allowed tags for chunk filtering, if None, all tags that are not excluded are allowed
        Inner list represents `or` condition, i.e. at least one tag from the inner list must be present.
        Lists in the outer list represent `and` condition, i.e. all inner lists `or` conditions must be satisfied.
        """,
    )
    excluded_tags: List[str] | None = Field(
        default=None,
        description="List of excluded tags for chunk filtering, in a case of conflict with allowed_tags, exclusion takes precedence",
    )
    allowed_content_types: List[Literal["markdown", "yaml", "yml", "json"]] | None = Field(
        default=None,
        description="List of allowed content types for chunk filtering, if None, all content types that are not excluded are allowed",
    )
    allow_different_app_name: bool = Field(
        default=False,
        description="Whether to allow chunks that mention a different application name than expected",
    )
    allow_unknown_app_version: bool = Field(
        default=True,
        description="Whether to allow chunks with unknown application version",
    )
    target_app_versions: List[str] | None = Field(
        default=None,
        description="List of target application versions to filter chunks by, if None, all versions are allowed",
    )

    @field_validator("allowed_categories", "excluded_categories")
    @classmethod
    def validate_categories(cls, v: List[str] | None) -> List[str] | None:
        """Validate that categories are from the configured list"""
        if v is None:
            return v

        invalid_categories = [cat for cat in v if cat not in config.scrape_and_process.chunk_categories]
        if invalid_categories:
            raise ValueError(
                f"Invalid categories: {invalid_categories}. "
                f"Must be one of: {config.scrape_and_process.chunk_categories}"
            )
        return v
