# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.modules.scrape.core.links import relative_paths_to_absolute


def test_relative_path_with_http_substring_is_resolved():
    """A relative path whose text contains 'http' must still be resolved to absolute.

    Regression: the previous substring test ("http" in link) misclassified such
    relative paths as already-absolute and left them unresolved, so they were
    later dropped by URL validation.
    """
    result, mapping = relative_paths_to_absolute(["/docs/http-status-codes"], "https://example.com/api/reference")

    assert result == ["https://example.com/docs/http-status-codes"]
    assert mapping["/docs/http-status-codes"] == "https://example.com/docs/http-status-codes"


def test_relative_path_preserves_query_string():
    """Query strings on relative references must survive resolution.

    Regression: resolving via urlparse(link).path dropped the query, so the
    scraper fetched the wrong (query-less) URL.
    """
    result, mapping = relative_paths_to_absolute(["/download?file=openapi.yaml"], "https://example.com/api/")

    assert result == ["https://example.com/download?file=openapi.yaml"]
    assert mapping["/download?file=openapi.yaml"] == "https://example.com/download?file=openapi.yaml"


def test_absolute_url_is_kept_unchanged():
    result, mapping = relative_paths_to_absolute(["https://other.example.com/page"], "https://example.com/api/")

    assert result == ["https://other.example.com/page"]
    assert mapping == {}


def test_absolute_url_with_port_is_not_mangled():
    result, mapping = relative_paths_to_absolute(["https://api.example.com:8443/v2/users"], "https://example.com/api/")

    assert result == ["https://api.example.com:8443/v2/users"]
    assert mapping == {}
