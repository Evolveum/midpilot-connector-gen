# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""
scim.cloud registry signal for apiType detection.

scim.cloud publishes community-maintained lists of products/services that
implement SCIM 1.1 and SCIM 2.0. We fetch those lists, cache them in-memory with
a TTL, and fuzzy-match the application name the user entered in discovery against
both the product name and the developer/vendor name.

The data files are served with a ``.json`` extension but are actually JavaScript
(``const scim_vX_implementations = { ... }``) and contain JS-style trailing
commas, so they are parsed leniently rather than with a strict JSON load.
"""

import asyncio
import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import List, Optional

import httpx
from pydantic import BaseModel, Field

from src.config import config

logger = logging.getLogger(__name__)

# Tokens stripped before token-subset comparison: corporate suffixes and
# SCIM/transport boilerplate that does not identify the product itself.
_STOPWORDS = {
    # corporate / legal suffixes
    "inc",
    "llc",
    "ltd",
    "limited",
    "incorporated",
    "gmbh",
    "corp",
    "co",
    "plc",
    "ag",
    "sa",
    "srl",
    "bv",
    "oy",
    "ab",
    "as",
    "kg",
    "kk",
    "technologies",
    "technology",
    "software",
    "systems",
    "solutions",
    "labs",
    # SCIM / protocol / transport boilerplate
    "scim",
    "scim2",
    "scimv2",
    "provisioning",
    "api",
    "sdk",
    "with",
    "for",
    "and",
    "the",
    "sso",
    "saml",
    "oauth",
    "oauth2",
    "oidc",
}


class ScimCloudImplementation(BaseModel):
    """A single SCIM implementation entry from scim.cloud."""

    project_name: str = Field(default="")
    client: str | bool = Field(default="")
    server: str | bool = Field(default="")
    open_source: str | bool = Field(default="")
    developer: str = Field(default="")
    link: str = Field(default="")
    scim_version: str = Field(default="")


class ScimCloudMatch(BaseModel):
    """Result of matching an application name against the scim.cloud registry."""

    matched: bool = Field(default=False, description="Whether the application was found in the registry.")
    application_name: str = Field(default="", description="The queried application name.")
    project_name: str = Field(default="", description="Matched scim.cloud product name.")
    developer: str = Field(default="", description="Matched scim.cloud developer/vendor.")
    scim_versions: List[str] = Field(default_factory=list, description="SCIM versions supported (e.g. '1.1', '2.0').")
    link: str = Field(default="", description="Reference link for the matched implementation.")
    matched_field: str = Field(default="", description="Which field matched: 'project_name' or 'developer'.")
    score: float = Field(default=0.0, description="Best match score in [0, 1].")


# Module-level TTL cache. The registry changes rarely, so caching avoids a remote
# fetch on every metadata extraction. Guarded by a lock to prevent stampedes.
_cache_implementations: Optional[List[ScimCloudImplementation]] = None
_cache_fetched_at: float = 0.0
_cache_lock = asyncio.Lock()


def _normalize(value: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    lowered = (value or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def _core_tokens(normalized: str) -> set[str]:
    """Significant tokens of a normalized string, excluding boilerplate stopwords."""
    return {token for token in normalized.split(" ") if token and token not in _STOPWORDS}


def _name_match_score(query_normalized: str, query_tokens: set[str], candidate: str) -> float:
    """
    Score how strongly a candidate name (product or developer) matches the query.

    Combines exact match, bidirectional token-subset match (after removing
    boilerplate), and fuzzy ratio so that user input that is shorter, longer, or
    slightly misspelled relative to the registry entry can still match.
    """
    candidate_normalized = _normalize(candidate)
    if not query_normalized or not candidate_normalized:
        return 0.0
    if query_normalized == candidate_normalized:
        return 1.0

    candidate_tokens = _core_tokens(candidate_normalized)
    if query_tokens and candidate_tokens:
        if query_tokens <= candidate_tokens:
            if any(len(token) >= 3 for token in query_tokens):
                return 0.95
        elif candidate_tokens <= query_tokens:
            if len(candidate_tokens) >= 2 and any(len(token) >= 3 for token in candidate_tokens):
                return 0.95

    ratio = SequenceMatcher(None, query_normalized, candidate_normalized).ratio()
    if query_tokens and candidate_tokens:
        ratio = max(
            ratio,
            SequenceMatcher(None, " ".join(sorted(query_tokens)), " ".join(sorted(candidate_tokens))).ratio(),
        )
    return ratio


def _is_server_capable(implementation: ScimCloudImplementation) -> bool:
    """Return whether the registry entry represents a SCIM server implementation."""
    server = implementation.server
    if isinstance(server, bool):
        return server
    return server.strip().lower() in {"yes", "true", "1"}


def parse_implementations(raw: str, scim_version: str) -> List[ScimCloudImplementation]:
    """
    Parse a scim.cloud ``scim_vX_implementations.json`` file.

    The file is a JS assignment with possible trailing commas, so the wrapper and
    trailing commas are stripped before a JSON load.
    """
    body = re.sub(r"^\s*const\s+\w+\s*=\s*", "", raw).strip()
    if body.endswith(";"):
        body = body[:-1]
    body = re.sub(r",(\s*[}\]])", r"\1", body)  # drop JS-style trailing commas

    data = json.loads(body)
    implementations = data.get("implementations", []) if isinstance(data, dict) else []
    parsed: List[ScimCloudImplementation] = []
    for item in implementations:
        if not isinstance(item, dict):
            continue
        parsed.append(ScimCloudImplementation(scim_version=scim_version, **item))
    return parsed


async def _fetch_source(client: httpx.AsyncClient, url: str, scim_version: str) -> List[ScimCloudImplementation]:
    response = await client.get(url)
    response.raise_for_status()
    return parse_implementations(response.text, scim_version)


async def _fetch_registry() -> List[ScimCloudImplementation]:
    """Fetch and parse both SCIM 1.1 and SCIM 2.0 registries; merge successes."""
    settings = config.digester
    sources = [
        (settings.scim_cloud_v1_url, "1.1"),
        (settings.scim_cloud_v2_url, "2.0"),
    ]
    timeout = settings.scim_cloud_fetch_timeout_seconds

    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *(_fetch_source(client, url, version) for url, version in sources),
            return_exceptions=True,
        )

    implementations: List[ScimCloudImplementation] = []
    for (url, _version), result in zip(sources, results):
        if isinstance(result, BaseException):
            logger.warning("[ApiType:ScimCloud] Failed to fetch/parse %s: %s", url, result)
            continue
        implementations.extend(result)

    if not implementations:
        raise RuntimeError("scim.cloud registry fetch produced no implementations")

    logger.info("[ApiType:ScimCloud] Loaded %s SCIM implementations from registry", len(implementations))
    return implementations


async def get_registry() -> List[ScimCloudImplementation]:
    """Return the cached registry, refreshing it when the TTL has expired."""
    global _cache_implementations, _cache_fetched_at

    ttl_seconds = config.digester.scim_cloud_cache_ttl.total_seconds()
    now = time.monotonic()
    if _cache_implementations is not None and (now - _cache_fetched_at) < ttl_seconds:
        return _cache_implementations

    async with _cache_lock:
        now = time.monotonic()
        if _cache_implementations is not None and (now - _cache_fetched_at) < ttl_seconds:
            return _cache_implementations
        try:
            implementations = await _fetch_registry()
        except Exception as exc:
            if _cache_implementations is not None:
                logger.warning("[ApiType:ScimCloud] Registry refresh failed, using stale cache: %s", exc)
                return _cache_implementations
            raise
        _cache_implementations = implementations
        _cache_fetched_at = time.monotonic()
        return implementations


def match_registry(application_name: str, implementations: List[ScimCloudImplementation]) -> ScimCloudMatch:
    """Find the best registry match for an application name across name and developer."""
    query_normalized = _normalize(application_name)
    if len(query_normalized) < 2:
        return ScimCloudMatch(application_name=application_name or "")

    query_tokens = _core_tokens(query_normalized)
    threshold = config.digester.scim_cloud_match_threshold

    best: Optional[ScimCloudImplementation] = None
    best_score = 0.0
    best_field = ""
    matched_versions: set[str] = set()

    for impl in implementations:
        if not _is_server_capable(impl):
            continue

        project_score = _name_match_score(query_normalized, query_tokens, impl.project_name)
        developer_score = _name_match_score(query_normalized, query_tokens, impl.developer)
        score, field = (
            (project_score, "project_name") if project_score >= developer_score else (developer_score, "developer")
        )
        if score < threshold:
            continue

        if impl.scim_version:
            matched_versions.add(impl.scim_version)
        if score > best_score:
            best_score = score
            best = impl
            best_field = field

    if best is None:
        return ScimCloudMatch(application_name=application_name)

    return ScimCloudMatch(
        matched=True,
        application_name=application_name,
        project_name=best.project_name,
        developer=best.developer,
        scim_versions=sorted(matched_versions),
        link=best.link,
        matched_field=best_field,
        score=round(best_score, 3),
    )


async def lookup_scim_support(application_name: str) -> ScimCloudMatch:
    """
    Check whether ``application_name`` appears in the scim.cloud registry.

    Returns a non-matching result (rather than raising) when the feature is
    disabled, the name is empty, or the registry cannot be loaded, so callers can
    safely fall back to documentation-based detection.
    """
    if not config.digester.scim_cloud_enabled:
        return ScimCloudMatch(application_name=application_name or "")
    if not application_name or not application_name.strip():
        logger.info("[ApiType:ScimCloud] No application name provided; skipping registry lookup")
        return ScimCloudMatch(application_name=application_name or "")

    try:
        implementations = await get_registry()
    except Exception as exc:
        logger.warning("[ApiType:ScimCloud] Registry unavailable, skipping signal: %s", exc)
        return ScimCloudMatch(application_name=application_name)

    match = match_registry(application_name, implementations)
    if match.matched:
        logger.info(
            "[ApiType:ScimCloud] '%s' matched '%s' (developer='%s', field=%s, score=%.3f, versions=%s)",
            application_name,
            match.project_name,
            match.developer,
            match.matched_field,
            match.score,
            match.scim_versions,
        )
    else:
        logger.info("[ApiType:ScimCloud] '%s' not found in registry", application_name)
    return match
