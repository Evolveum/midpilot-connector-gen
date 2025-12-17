# Copyright (c) 2025 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import uuid
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field


class Session(BaseModel):
    """
    Canonical on-disk session model (camelCase).
    Accepts snake_case on input for backwards compatibility.
    """

    sessionId: UUID = Field(..., description="UUID v4", validation_alias=AliasChoices("sessionId", "session_id"))
    createdAt: str = Field(
        ..., description="Session creation time (ISO 8601)", validation_alias=AliasChoices("createdAt", "created_at")
    )
    updatedAt: str = Field(
        ..., description="Session update time (ISO 8601)", validation_alias=AliasChoices("updatedAt", "updated_at")
    )
    data: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary session payload")


class DocumentationItem(BaseModel):
    """Unified documentation item that can come from scraper or user upload."""

    id: UUID = Field(
        default_factory=uuid.uuid4,
        serialization_alias="uuid",
        validation_alias=AliasChoices("uuid", "id"),
        description="Unique identifier for this documentation piece",
    )
    page_id: Optional[UUID] = Field(
        None,
        serialization_alias="pageId",
        validation_alias=AliasChoices("pageId", "page_id"),
        description="Page ID from scraper, null for uploads",
    )
    source: str = Field(..., description="Source type: 'scraper' or 'upload'")
    url: Optional[str] = Field(None, description="URL for scraped documentation, null for uploads")
    summary: Optional[str] = Field(None, description="Summary of the content")
    content: str = Field(..., description="The actual documentation text")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        serialization_alias="@metadata",
        validation_alias=AliasChoices("@metadata", "metadata"),
        description="Additional metadata - for scraped: chunk_number, num_endpoints, length, contentType, tags, category, llm_tags, llm_category; for uploads: filename, length",
    )


class SessionCreateResponse(BaseModel):
    """Response model for session creation."""

    sessionId: UUID = Field(..., description="The unique identifier for the created session")
    message: str = Field(..., description="Confirmation message about the session creation")


class SessionDataResponse(BaseModel):
    """Response model for session data retrieval."""

    sessionId: UUID = Field(..., description="The unique identifier for the session")
    data: Dict[str, Any] = Field(..., description="The session data stored as a dictionary")
    createdAt: str = Field(..., description="Timestamp when the session was created")
    updatedAt: str = Field(..., description="Timestamp when the session was last updated")


class SessionUpdateRequest(BaseModel):
    """Request model for updating session data."""

    data: Dict[str, Any] = Field(..., description="Dictionary containing the data to update in the session")
