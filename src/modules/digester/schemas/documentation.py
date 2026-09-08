# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Read-only source documentation contracts for the wizard."""

from src.core.schema import CamelCaseModel
from src.documents.schemas import RelevantDocumentationItem
from src.modules.digester.schemas.common import NormalizedEndpointMethod


class DocumentationEndpoint(CamelCaseModel):
    method: NormalizedEndpointMethod
    path: str


class RelevantDocumentationResponse(CamelCaseModel):
    object_class: str
    endpoint: DocumentationEndpoint | None
    items: list[RelevantDocumentationItem]
    next_offset: int | None
