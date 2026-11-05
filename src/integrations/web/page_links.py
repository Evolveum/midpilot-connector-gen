# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Outgoing links of a rendered page, normalized for crawling.

crawl4ai lists every ``<a href>`` of the page, navigation included, in ``CrawlResult.links`` before any
content is removed. Each link is made absolute, stripped of its fragment, tracking parameters and trailing
slash (the form the scraper keys documents by), and deduplicated with the first non-empty link text kept.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from src.integrations.web.urls import canonicalize_url

# Static assets and downloads that are never documentation pages.
_SKIPPED_EXTENSIONS = (
    ".7z",
    ".atom",
    ".css",
    ".dmg",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".map",
    ".mjs",
    ".mp3",
    ".mp4",
    ".msi",
    ".png",
    ".rss",
    ".svg",
    ".tar",
    ".tgz",
    ".ttf",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
)
_MAX_TEXT_CHARS = 120
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class PageLink:
    """One outgoing link of a page: its normalized URL and the link text a reader sees."""

    url: str
    text: str = ""


def normalize_link_url(href: str, page_url: str) -> str | None:
    """Absolute http(s) URL of ``href`` without fragment, tracking parameters and trailing slash, or None."""
    absolute = urljoin(page_url, href.strip())
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.path.lower().endswith(_SKIPPED_EXTENSIONS):
        return None
    return canonicalize_url(absolute).rstrip("/")


def page_links(links: Mapping[str, Any] | None, page_url: str) -> list[PageLink]:
    """Normalized outgoing links of a page in page order, without the page itself. CPU-bound on large pages."""
    own_url = normalize_link_url(page_url, page_url)
    texts: dict[str, str] = {}
    for group in ("internal", "external"):
        for link in (links or {}).get(group) or []:
            if not isinstance(link, Mapping) or not isinstance(link.get("href"), str):
                continue
            url = normalize_link_url(link["href"], page_url)
            if url is None or url == own_url:
                continue
            text = _link_text(link)
            if not texts.get(url):
                texts[url] = text
    return [PageLink(url, text) for url, text in texts.items()]


def _link_text(link: Mapping[str, Any]) -> str:
    for key in ("text", "title"):
        value = link.get(key)
        if isinstance(value, str) and (text := _WHITESPACE.sub(" ", value).strip()):
            return text[:_MAX_TEXT_CHARS]
    return ""
