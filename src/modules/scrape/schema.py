# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Any, List, Literal

from pydantic import BaseModel, Field, field_validator

from src.core.schema import CamelCaseModel
from src.session.schema import Documentation

__all__ = [
    "PAGE_COVERAGE_TOPICS",
    "PAGE_ROLES",
    "LinkSelection",
    "PageAssessment",
    "ScrapeJobInput",
    "ScrapeRequest",
    "ScrapeResult",
]


class ScrapeRequest(CamelCaseModel):
    """
    Input payload to start the scrape job.
    """

    starter_links: List[str] = Field(..., description="Initial URLs to scrape")
    application_name: str = Field(..., description="Application name")
    application_version: str = Field(
        default="current",
        description="Application version. If omitted, discoveryInput.applicationVersion is used when available, otherwise 'current'.",
    )
    skip_cache: bool = Field(
        False,
        description="Whether to skip already processed data from a different session when scraper input is the same.",
    )


class ScrapeResult(CamelCaseModel):
    finish_reason: str
    saved_documentations_count: int
    saved_chunks_count: int
    saved_documentations: List[Documentation]


class ScrapeJobInput(ScrapeRequest):
    """Input of a scrape job: the request plus what the orchestration resolved when it scheduled the job.

    Stored in ``jobs.input`` and ``scrapeInput``, so the resolved values are part of the cache key and do not
    change while the job waits in the queue. Jobs queued before 1.1.0 have no ``effectiveApplicationVersion``.
    """

    effective_application_version: str | None = Field(
        None,
        description=(
            "Concrete application version the scrape targets: applicationVersion, else "
            "discoveryInput.applicationVersion, else 'latest' when neither names a concrete version."
        ),
    )


PAGE_ROLES = (
    "api_reference",
    "api_overview",
    "api_specification",
    "authentication",
    "scim",
    "admin_guide",
    "developer_guide",
    "navigation",
    "release_notes",
    "end_user_help",
    "marketing",
    "community",
    "other",
)
PAGE_COVERAGE_TOPICS = (
    "authentication",
    "users",
    "groups",
    "roles",
    "permissions",
    "organizations",
    "object_schema",
    "endpoints",
    "pagination",
    "filtering",
    "errors",
    "rate_limits",
    "scim",
    "api_specification",
)
_MAX_MENTIONED_OBJECT_CLASSES = 30


def _verdict(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in ("yes", "no") else "unclear"


class PageAssessment(BaseModel):
    """LLM output of the page assessment (``Scrape:Pages:Assess``). Lenient: unknown values become neutral."""

    keep: bool = Field(description="Whether the page is documentation the connector developer needs")
    role: str = Field(description=f"What the page is; one of {', '.join(PAGE_ROLES)}")
    covers: List[str] = Field(
        default_factory=list, description=f"Topics the page documents; from {', '.join(PAGE_COVERAGE_TOPICS)}"
    )
    mentioned_object_classes: List[str] = Field(
        default_factory=list, description="Kinds of records of the application the page mentions, singular"
    )
    product_match: Literal["yes", "no", "unclear"] = Field(description="Whether the page documents the application")
    product_evidence: str = Field("", description="Verbatim quote from the page that supports product_match")
    version_match: Literal["yes", "no", "unclear"] = Field(description="Whether the page documents the target version")
    version_evidence: str = Field("", description="Verbatim quote from the page that supports version_match")

    @field_validator("role", mode="before")
    @classmethod
    def _known_role(cls, value: Any) -> str:
        role = str(value or "").strip().lower()
        return role if role in PAGE_ROLES else "other"

    @field_validator("covers", mode="before")
    @classmethod
    def _known_topics(cls, value: Any) -> list[str]:
        topics = [str(item).strip().lower() for item in value] if isinstance(value, list) else []
        return list(dict.fromkeys(topic for topic in topics if topic in PAGE_COVERAGE_TOPICS))

    @field_validator("mentioned_object_classes", mode="before")
    @classmethod
    def _class_names(cls, value: Any) -> list[str]:
        names = [item.strip() for item in value if isinstance(item, str)] if isinstance(value, list) else []
        return list(dict.fromkeys(name for name in names if name))[:_MAX_MENTIONED_OBJECT_CLASSES]

    @field_validator("product_match", "version_match", mode="before")
    @classmethod
    def _known_verdict(cls, value: Any) -> str:
        return _verdict(value)

    @field_validator("product_evidence", "version_evidence", mode="before")
    @classmethod
    def _evidence_text(cls, value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""


class LinkSelection(BaseModel):
    """LLM output of the link selection (``Scrape:Links:Select``): indices of the links to follow."""

    selected: List[int] = Field(default_factory=list, description="Indices of the links to follow")

    @field_validator("selected", mode="before")
    @classmethod
    def _indices(cls, value: Any) -> list[int]:
        items = value if isinstance(value, list) else []
        return [
            int(item)
            for item in items
            if (isinstance(item, int) and not isinstance(item, bool)) or (isinstance(item, str) and item.isdigit())
        ]
