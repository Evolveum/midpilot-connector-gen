# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from datetime import timedelta

from pydantic import BaseModel, Field


class SearchSettings(BaseModel):
    """Search method specification of Discovery module."""

    method_name: str = ""
    discovery_input_check_interval: timedelta = Field(
        timedelta(weeks=4),
        description="Time interval for checking if the same discovery input has been processed before.",
    )


class BraveSettings(BaseModel):
    """Configuration for Brave Search API."""

    api_key: str = ""
    endpoint: str = "https://api.search.brave.com/res/v1/web/search"
