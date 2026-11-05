# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from unittest.mock import patch

import pytest

from src.integrations.web.browser import build_browser_config
from src.integrations.web.robots import robots_allowed


class _FakeRobotsParser:
    calls: list[tuple[str, str]] = []

    async def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        self.calls.append((url, user_agent))
        return "/server/stable/" in url or "/server/" not in url


@pytest.mark.asyncio
async def test_robots_check_answers_per_url_with_the_crawler_user_agent() -> None:
    _FakeRobotsParser.calls = []
    urls = [
        "https://docs.nextcloud.com/server/stable/admin_manual/",
        "https://docs.nextcloud.com/server/31/developer_manual/",
        "https://example.test/api",
        "https://docs.nextcloud.com/server/stable/admin_manual/",
    ]
    with patch("src.integrations.web.robots.RobotsParser", _FakeRobotsParser):
        allowed = await robots_allowed(urls)

    assert allowed == {urls[0]: True, urls[1]: False, urls[2]: True}
    assert {user_agent for _, user_agent in _FakeRobotsParser.calls} == {build_browser_config(verbose=False).user_agent}
    assert len(_FakeRobotsParser.calls) == 3  # each distinct URL once
