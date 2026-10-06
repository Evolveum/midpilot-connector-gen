# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Chromium configuration for the same outbound proxy environment as HTTP clients."""

from urllib.request import getproxies

from crawl4ai.async_configs import BrowserConfig  # type: ignore


def build_browser_config(*, verbose: bool) -> BrowserConfig:
    proxies = getproxies()
    # Chromium does not consume HTTP_PROXY/HTTPS_PROXY itself. Use its per-scheme
    # mapping so HTTPS destinations still use the HTTP CONNECT proxy correctly.
    mappings = [
        f"{scheme}={proxy}" for scheme in ("http", "https") if (proxy := proxies.get(scheme) or proxies.get("all"))
    ]
    args: list[str] = []
    if mappings:
        args.append("--proxy-server=" + ";".join(mappings))
        # Chromium expects semicolon-separated patterns and *.domain suffixes.
        bypass = [
            "*" + entry if entry.startswith(".") else entry
            for raw in proxies.get("no", "").split(",")
            if (entry := raw.strip())
        ]
        if bypass:
            args.append("--proxy-bypass-list=" + ";".join(bypass))
    return BrowserConfig(verbose=verbose, extra_args=args)
