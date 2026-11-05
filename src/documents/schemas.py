# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import uuid
from collections.abc import Mapping
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from src.core.schema import CamelCaseModel
from src.documents.spec_validation import SpecDescription


class RelevantDocumentationItem(CamelCaseModel):
    """Stored source chunk and its document metadata, suitable for read-only display."""

    doc_id: uuid.UUID
    chunk_id: uuid.UUID
    content: str
    source: Literal["scraper", "upload"]
    url: str | None = None
    filename: str | None = None
    content_type: str | None = None
    chunk_number: int | None = None


def normalize_count(value: Any) -> Any:
    """Normalize nullish LLM count outputs to zero before Pydantic integer parsing."""
    if value is None:
        return 0
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "unknown", "n/a"}:
        return 0
    return value


class SpecRetrievalMetadata(BaseModel):
    """The API description a scraped data file was recognized as."""

    format: str
    title: str | None = None
    version: str | None = None
    canonical_hash: str

    @classmethod
    def from_description(cls, description: SpecDescription) -> "SpecRetrievalMetadata":
        return cls(
            format=description.format,
            title=description.title,
            version=description.version,
            canonical_hash=description.canonical_hash,
        )


MatchVerdict = Literal["yes", "no", "unclear"]


class PageAssessmentMetadata(BaseModel):
    """What the scraper's page assessment found out about a scraped page.

    ``product_match`` and ``version_match`` are ``"no"`` only with evidence quoted from the page, which is
    kept next to the verdict; without such evidence the verdict is ``"unclear"``. ``version_match`` is
    ``"unclear"`` when the scrape targets no concrete version.
    """

    role: str
    product_match: MatchVerdict = "unclear"
    product_evidence: str | None = None
    version_match: MatchVerdict = "unclear"
    version_evidence: str | None = None
    covers: List[str] = Field(default_factory=list)
    object_classes: List[str] = Field(default_factory=list)


class RetrievalMetadata(BaseModel):
    """How the scraper obtained a document; stored as ``metadata["retrieval"]`` on each of its chunks.

    Chunks without it (uploads and documentation scraped before 1.1.0) are neutral: no selection rule
    excludes them for the missing key. ``depth`` counts the link hops from a starter link; ``assessment``
    is present for a page the scraper assessed before saving it.
    """

    version: Literal[1] = 1
    starter: bool = False
    depth: int | None = None
    spec: SpecRetrievalMetadata | None = None
    assessment: PageAssessmentMetadata | None = None

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "RetrievalMetadata | None":
        raw = (metadata or {}).get("retrieval")
        if not isinstance(raw, Mapping):
            return None
        try:
            return cls.model_validate(raw)
        except ValidationError:
            return None


class SavedDocumentation(CamelCaseModel):
    """
    Scraped or uploaded documentation prepared for chunk processing.
    """

    url: str
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    content_type: Optional[str] = None
    content: Optional[str] = None
    retrieval: Optional[RetrievalMetadata] = None
