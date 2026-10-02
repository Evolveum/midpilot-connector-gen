# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Local proxy checks; no external network or API credentials are needed."""

import pytest
from aiohttp import web

from src.integrations.web.browser import build_browser_config
from src.integrations.web.fetch import fetch_data_documentation, get_content_type


@pytest.mark.asyncio
async def test_document_fetch_uses_environment_proxy(monkeypatch):
    seen = []

    async def proxy(request):
        seen.append((request.method, request.raw_path))
        return web.Response(text='{"proxied": true}', content_type="application/json")

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", proxy)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("no_proxy", "")
    try:
        url = "http://unresolvable.invalid/schema.json"
        assert await get_content_type(url) == "application/json; charset=utf-8"
        assert await fetch_data_documentation(url) == (url, '{"proxied": true}')
        assert [method for method, _ in seen] == ["HEAD", "GET"]
        assert all(path == url for _, path in seen)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_document_fetch_bypasses_proxy_for_no_proxy(monkeypatch):
    async def direct(request):
        return web.Response(text='{"direct": true}', content_type="application/json")

    app = web.Application()
    app.router.add_get("/schema.json", direct)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        url = f"http://127.0.0.1:{port}/schema.json"
        assert await fetch_data_documentation(url) == (url, '{"direct": true}')
    finally:
        await runner.cleanup()


def test_browser_config_translates_environment(monkeypatch):
    monkeypatch.setattr(
        "src.integrations.web.browser.getproxies",
        lambda: {"http": "http://proxy:3128", "https": "http://proxy:3128", "no": "localhost,.e,10.42.0.0/16"},
    )
    config = build_browser_config(verbose=False)
    assert "--proxy-server=http=http://proxy:3128;https=http://proxy:3128" in config.extra_args
    assert "--proxy-bypass-list=localhost;*.e;10.42.0.0/16" in config.extra_args


def test_browser_config_without_proxy(monkeypatch):
    monkeypatch.setattr("src.integrations.web.browser.getproxies", lambda: {})
    assert build_browser_config(verbose=False).extra_args == []
