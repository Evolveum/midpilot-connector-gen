# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from types import SimpleNamespace

import pytest

from src.integrations.web.fetch import read_text_limited


def _response(body: bytes, *, content_length: int | None = None, charset: str | None = "utf-8"):
    async def iter_chunked(size: int):
        for start in range(0, len(body), size):
            yield body[start : start + size]

    return SimpleNamespace(
        content_length=content_length, charset=charset, content=SimpleNamespace(iter_chunked=iter_chunked)
    )


@pytest.mark.asyncio
async def test_body_within_the_limit_is_decoded() -> None:
    assert await read_text_limited(_response("Grüße".encode()), 100) == "Grüße"
    assert await read_text_limited(_response(b"plain", charset="no-such-charset"), 100) == "plain"


@pytest.mark.asyncio
async def test_body_over_the_limit_is_rejected_by_header_or_while_streaming() -> None:
    assert await read_text_limited(_response(b"x", content_length=10_000), 100) is None
    assert await read_text_limited(_response(b"x" * 200_000), 100_000) is None
