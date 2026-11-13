# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""robots.txt checks that give the same answer the scraper's crawler gets.

The crawler (crawl4ai with ``check_robots_txt``) refuses URLs the site's robots.txt disallows for its user
agent. Discovery uses this check to leave out candidate links the scraper could not fetch.
"""

import asyncio
from collections import defaultdict
from collections.abc import Sequence

from crawl4ai.utils import RobotsParser  # type: ignore

from src.integrations.web.browser import build_browser_config
from src.integrations.web.urls import url_host


async def robots_allowed(urls: Sequence[str]) -> dict[str, bool]:
    """Whether the scraper's crawler may fetch each URL under its site's robots.txt.

    Uses crawl4ai's robots parser, including its robots.txt cache, with the crawler's user agent. A robots.txt
    that cannot be read allows everything, as in crawl4ai. URLs of one host are checked one after another, so
    its robots.txt is downloaded once.
    """
    parser = RobotsParser()
    user_agent = build_browser_config(verbose=False).user_agent
    by_host: defaultdict[str, list[str]] = defaultdict(list)
    for url in dict.fromkeys(urls):
        by_host[url_host(url)].append(url)

    async def check_host(host_urls: list[str]) -> list[tuple[str, bool]]:
        return [(url, await parser.can_fetch(url, user_agent)) for url in host_urls]

    checked = await asyncio.gather(*(check_host(host_urls) for host_urls in by_host.values()))
    return dict(result for host_results in checked for result in host_results)
