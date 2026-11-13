# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Crawl scope and deterministic ranking of candidate links.

The scope follows the starter links the user picked: their hosts, and with a lower rank the other hosts of
the same site (``community.example.com`` next to ``docs.example.com``), whose pages still pass the product
check of the page assessment. On shared hosting (``vendor.github.io``) the site is the exact host, and on
GitHub it is the owner of the starter repository (``github.com/<owner>/``).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.integrations.web.page_links import PageLink
from src.integrations.web.urls import site_of, url_host

_GITHUB_HOST = "github.com"
_RAW_GITHUB_HOST = "raw.githubusercontent.com"
# File names in a starter path; the directory holding such a file is the starter's section.
_FILE_NAME = re.compile(r"\.[a-z0-9]{1,5}$")
# Words in a link text or URL that point at what an IDM connector developer needs.
_IDM_TOKENS = (
    "account",
    "admin",
    "api",
    "auth",
    "developer",
    "endpoint",
    "group",
    "identity",
    "member",
    "oauth",
    "openapi",
    "permission",
    "provision",
    "reference",
    "resource",
    "rest",
    "role",
    "schema",
    "scim",
    "spec",
    "swagger",
    "token",
    "user",
)
_WORD = re.compile(r"[a-z0-9]+")

STARTER_SECTION = 0
STARTER_HOST = 1
STARTER_SITE = 2


@dataclass(frozen=True)
class CrawlScope:
    """Which URLs the scraper may crawl, derived from the starter links."""

    starter_hosts: frozenset[str]
    starter_sections: tuple[tuple[str, str], ...]
    starter_sites: frozenset[str]
    github_owners: frozenset[str]

    @classmethod
    def from_starter_links(cls, starter_links: Iterable[str]) -> "CrawlScope":
        hosts: set[str] = set()
        sections: list[tuple[str, str]] = []
        sites: set[str] = set()
        owners: set[str] = set()
        for link in starter_links:
            host = url_host(link)
            if not host:
                continue
            path = urlsplit(link.strip()).path or "/"
            hosts.add(host)
            sections.append((host, _section_of(path)))
            if host == _GITHUB_HOST:
                if owner := _github_owner(path):
                    owners.add(owner)
            else:
                sites.add(site_of(link))
        return cls(frozenset(hosts), tuple(dict.fromkeys(sections)), frozenset(sites), frozenset(owners))

    def rank(self, url: str) -> int | None:
        """``STARTER_SECTION``, ``STARTER_HOST`` or ``STARTER_SITE`` for an in-scope URL (lower is closer), else None."""
        host = url_host(url)
        path = urlsplit(url).path or "/"
        if host == _GITHUB_HOST and _github_owner(path) not in self.github_owners:
            return None
        if host in self.starter_hosts:
            directory = path if path.endswith("/") else f"{path}/"
            in_section = any(
                host == section_host and directory.startswith(section)
                for section_host, section in self.starter_sections
            )
            return STARTER_SECTION if in_section else STARTER_HOST
        if host != _GITHUB_HOST and site_of(url) in self.starter_sites:
            return STARTER_SITE
        return None

    def allows_api_description(self, url: str) -> bool:
        """Whether an API description a page references may be fetched: in scope, or a raw GitHub file."""
        return self.rank(url) is not None or url_host(url) == _RAW_GITHUB_HOST

    def describe(self) -> str:
        return ", ".join(sorted(self.starter_hosts | self.starter_sites))


def rank_candidate_links(links: Iterable[PageLink], scope: CrawlScope) -> list[PageLink]:
    """In-scope links, closest to the starter links and most IDM-related first; out-of-scope links are dropped."""
    ranked: list[tuple[int, int, int, PageLink]] = []
    for link in links:
        rank = scope.rank(link.url)
        if rank is not None:
            ranked.append((rank, -_idm_score(link), len(link.url), link))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def _idm_score(link: PageLink) -> int:
    words = _WORD.findall(f"{urlsplit(link.url).path} {link.text}".lower())
    return sum(1 for token in _IDM_TOKENS if any(word.startswith(token) for word in words))


def _section_of(path: str) -> str:
    """The directory a starter path belongs to: the path itself, or the directory of a file name in it."""
    if path.endswith("/"):
        return path
    head, _, last = path.rpartition("/")
    return f"{head}/" if _FILE_NAME.search(last) else f"{path}/"


def _github_owner(path: str) -> str | None:
    segments: Sequence[str] = [segment for segment in path.split("/") if segment]
    return segments[0].lower() if segments else None
