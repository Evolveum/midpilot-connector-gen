# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from pydantic import BaseModel, ConfigDict, Field


class SearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., description="Result title")
    href: str = Field(..., description="Result URL")
    body: str = Field(..., description="Result summary/snippet")
    source: str = Field(..., description="Search backend source")
