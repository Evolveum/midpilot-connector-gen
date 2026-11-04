# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""URL helpers shared by discovery and scrape: canonical form and path-based blocklist matching."""

from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

_TRACKING_PARAMS = {"gclid", "fbclid", "yclid", "mc_cid", "mc_eid"}


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL for deduplication: drop the fragment and tracking query parameters."""
    try:
        parts = urlsplit(url.strip())
        keep_params: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered.startswith("utm_") or lowered in _TRACKING_PARAMS:
                continue
            keep_params.append((key, value))

        new_query = urlencode(keep_params, doseq=True)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))
    except Exception:
        return url.strip()


def is_forbidden_url(url: str, forbidden_url_parts: list[str]) -> bool:
    """Return True when a forbidden part matches a whole path segment, the file name or a dotted name part.

    Matching whole segments instead of substrings keeps e.g. ``/api/contacts`` when ``contact`` is forbidden.
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    filename = path.rsplit("/", 1)[-1]
    path_segments = [segment for segment in path.split("/") if segment]

    for forbidden_part in forbidden_url_parts:
        normalized_part = forbidden_part.strip().lower().strip("/")
        if not normalized_part:
            continue
        if (
            normalized_part in path_segments
            or normalized_part == filename
            or f"/{normalized_part}/" in path
            or path.endswith(f"/{normalized_part}")
            or f".{normalized_part}." in path
            or path.endswith(f".{normalized_part}")
        ):
            return True
    return False
