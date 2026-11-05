# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Locate the machine-readable API descriptions a documentation page is built from or links to.

API reference portals (Swagger UI, Redoc, Stoplight Elements, RapiDoc, Scalar) render an OpenAPI
document in the browser; the rendered page is a lossy copy of it. These helpers return the URL of the
document itself so the scraper can fetch it.
"""

import asyncio
import logging
import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup

from src.integrations.web.fetch import read_text_limited

logger = logging.getLogger(__name__)

_SPEC_FILE_NAME = re.compile(r"(openapi|swagger)[^/]*\.(json|ya?ml)$", re.IGNORECASE)
_DATA_FILE_NAME = re.compile(r"\.(json|ya?ml)$", re.IGNORECASE)
_SPEC_PATH_SUFFIXES = ("/v2/api-docs", "/v3/api-docs")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_BLOB_PATH = re.compile(r"^/([^/]+)/([^/]+)/blob/(.+)$")

# Viewer elements and the attribute holding the spec URL (html.parser lower-cases attribute names).
_VIEWER_SPEC_ATTRIBUTES = (
    ("redoc", "spec-url"),
    ("rapi-doc", "spec-url"),
    ("openapi-explorer", "spec-url"),
    ("elements-api", "apidescriptionurl"),
    ("script#api-reference", "data-url"),
)
_SPEC_LINK_TYPES = ("openapi", "swagger", "json", "yaml")
# Swagger UI configuration: ``url: "..."`` and each ``urls: [{url: "..."}]`` entry, in JS or JSON.
_SWAGGER_URL = re.compile(r"""\burl["']?\s*:\s*["'`]([^"'`]+)["'`]""")
_SWAGGER_CONFIG_URL = re.compile(r"""\bconfigUrl["']?\s*:\s*["'`]([^"'`]+)["'`]""")
_REDOC_INIT_URL = re.compile(r"""Redoc\.init\(\s*["'`]([^"'`]+)["'`]""")
_SWAGGER_UI_MARKERS = ("SwaggerUIBundle", "SwaggerUIStandalonePreset", "swagger-ui")
_SWAGGER_INITIALIZER_NAME = "swagger-initializer.js"
_MAX_CONFIG_FETCHES = 3
_MAX_CONFIG_BYTES = 1024 * 1024
_CONFIG_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=15)


def looks_like_spec_url(url: str) -> bool:
    """True for URLs that name an OpenAPI/Swagger document (``openapi.json``, ``/v3/api-docs``, ...)."""
    path = urlsplit(url).path.rstrip("/").lower()
    return bool(_SPEC_FILE_NAME.search(path.rsplit("/", 1)[-1])) or path.endswith(_SPEC_PATH_SUFFIXES)


def to_raw_file_url(url: str) -> str:
    """Map a GitHub file page (``github.com/<org>/<repo>/blob/<ref>/<path>``) to the raw file; other URLs as is."""
    parts = urlsplit(url)
    match = _GITHUB_BLOB_PATH.match(parts.path) if parts.netloc.lower() in _GITHUB_HOSTS else None
    if match is None:
        return url
    owner, repository, ref_and_path = match.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repository}/{ref_and_path}"


async def discover_spec_urls(html: str, page_url: str, page_links: Iterable[str]) -> list[str]:
    """Return the API description URLs a rendered page embeds or links to, in discovery order.

    Covers viewer configuration (Swagger UI including its ``swagger-initializer.js`` and ``configUrl``,
    Redoc, Stoplight Elements, RapiDoc, Scalar), ``<link rel="alternate">``, JSON/YAML download links and
    links whose URL names a spec. GitHub file pages are mapped to their raw file. Never returns the page
    itself.
    """
    spec_urls, config_urls = await asyncio.to_thread(_find_page_references, html, page_url, list(page_links))
    if config_urls:
        spec_urls.extend(await _resolve_swagger_configs(config_urls))
    page = page_url.rstrip("/")
    return [url for url in dict.fromkeys(to_raw_file_url(url) for url in spec_urls) if url.rstrip("/") != page]


def _find_page_references(html: str, page_url: str, page_links: list[str]) -> tuple[list[str], list[str]]:
    """Static part of the discovery: spec URLs, and Swagger UI configuration files that still have to be read."""
    soup = BeautifulSoup(html or "", "html.parser")
    spec_urls: list[str] = []
    config_urls: list[str] = []

    def add(found: list[str], value: object) -> None:
        if isinstance(value, str) and value.strip():
            absolute = urljoin(page_url, value.strip())
            if urlsplit(absolute).scheme in ("http", "https"):
                found.append(absolute)

    for selector, attribute in _VIEWER_SPEC_ATTRIBUTES:
        for element in soup.select(f"{selector}[{attribute}]"):
            add(spec_urls, element.get(attribute))
    for link in soup.select("link[rel~=alternate][href][type]"):
        if any(marker in str(link.get("type", "")).lower() for marker in _SPEC_LINK_TYPES):
            add(spec_urls, link.get("href"))
    # Viewers offer their spec as a download ("Download OpenAPI description" in Redoc).
    for anchor in soup.select("a[download][href]"):
        href = anchor.get("href")
        if isinstance(href, str) and _DATA_FILE_NAME.search(urlsplit(href).path):
            add(spec_urls, href)

    is_swagger_ui = any(marker in html for marker in _SWAGGER_UI_MARKERS)
    for script in soup.find_all("script"):
        source = script.get("src")
        if isinstance(source, str) and source.split("?", 1)[0].endswith(_SWAGGER_INITIALIZER_NAME):
            add(config_urls, source)
            continue
        text = script.string or ""
        for value in _REDOC_INIT_URL.findall(text):
            add(spec_urls, value)
        if "SwaggerUIBundle" in text or "SwaggerUI(" in text:
            for value in _SWAGGER_URL.findall(text):
                add(spec_urls, value)
            for value in _SWAGGER_CONFIG_URL.findall(text):
                add(config_urls, value)
    if is_swagger_ui:
        # Swagger UI loads the spec named by its own ``?url=`` query parameter.
        for value in parse_qs(urlsplit(page_url).query).get("url", []):
            add(spec_urls, value)

    for link_url in [page_url, *page_links]:
        if looks_like_spec_url(link_url):
            spec_urls.append(link_url)
    return spec_urls, config_urls


async def _resolve_swagger_configs(config_urls: list[str]) -> list[str]:
    """Read Swagger UI configuration files (initializer script, ``configUrl`` JSON) for the spec URLs in them."""
    spec_urls: list[str] = []
    pending = list(dict.fromkeys(config_urls))
    visited: set[str] = set()
    async with aiohttp.ClientSession(trust_env=True, timeout=_CONFIG_FETCH_TIMEOUT) as http_session:
        while pending and len(visited) < _MAX_CONFIG_FETCHES:
            config_url = pending.pop(0)
            if config_url in visited:
                continue
            visited.add(config_url)
            text = await _fetch_config(http_session, config_url)
            if text is None:
                continue
            spec_urls.extend(urljoin(config_url, value) for value in _SWAGGER_URL.findall(text))
            pending.extend(urljoin(config_url, value) for value in _SWAGGER_CONFIG_URL.findall(text))
    return spec_urls


async def _fetch_config(http_session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with http_session.get(url) as response:
            if response.status != 200:
                logger.debug("[Scrape:Specs] Swagger UI configuration %s returned HTTP %s", url, response.status)
                return None
            return await read_text_limited(response, _MAX_CONFIG_BYTES)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("[Scrape:Specs] Swagger UI configuration %s could not be read: %s", url, exc)
        return None
