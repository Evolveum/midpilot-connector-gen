# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.web import IrrelevantLinks
from src.modules.discovery.utils.discovery_helpers import drop_robots_denied
from src.modules.discovery.utils.filter_helpers import filter_candidate_links


@pytest.mark.asyncio
async def test_blocklist_keeps_api_pages_whose_path_only_contains_a_blocked_word() -> None:
    """Regression: substring matching dropped e.g. a CRM Contacts API page because it contains "contact"."""
    candidates = [
        {"href": "https://developer.example.com/api/contacts", "title": "Contacts API", "body": "List contacts"},
        {"href": "https://www.example.com/contact", "title": "Contact us", "body": "Talk to sales"},
    ]
    with patch(
        "src.modules.discovery.utils.filter_helpers.classify_irrelevant_links",
        new_callable=AsyncMock,
        return_value=IrrelevantLinks(links=[]),
    ):
        relevant, irrelevant = await filter_candidate_links(candidates, app="Example", app_version="1", max_llm_calls=1)

    assert relevant == ["https://developer.example.com/api/contacts"]
    assert irrelevant == ["https://www.example.com/contact"]


@pytest.mark.asyncio
async def test_candidates_that_robots_txt_forbids_the_scraper_are_dropped() -> None:
    candidates = [
        {"href": "https://docs.nextcloud.com/server/stable/admin_manual/user_provisioning_api.html", "title": "a"},
        {"href": "https://docs.nextcloud.com/server/14/admin_manual/user_provisioning_api.html", "title": "b"},
        {"title": "no link"},
    ]
    allowed = {candidates[0]["href"]: True, candidates[1]["href"]: False}
    with patch("src.modules.discovery.utils.discovery_helpers.robots_allowed", AsyncMock(return_value=allowed)):
        kept = await drop_robots_denied(candidates)

    assert kept == [candidates[0], candidates[2]]
