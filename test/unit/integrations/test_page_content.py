# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.integrations.web.page_content import extract_main_content

SELECTORS = ["article", "main", "[role=main]"]
URL = "https://docs.example.test/api/users"
BODY_TEXT = "The users resource lists every account of the tenant. " * 5


def _page(main: str) -> str:
    return f"""<html><head><script>track()</script></head><body>
    <header>Product menu Pricing Sign in</header>
    <nav><a href="/api/groups">Groups</a><a href="/api/roles">Roles</a></nav>
    {main}
    <div id="onetrust-consent-sdk"><p>We use cookies to improve your experience.</p></div>
    <footer>Copyright Example Inc.</footer>
    </body></html>"""


def test_reference_tables_and_code_of_the_main_element_are_kept() -> None:
    markdown = extract_main_content(
        _page(f"""<main><h1>Users</h1><p>{BODY_TEXT}</p>
        <table><tr><th>Attribute</th><th>Type</th></tr><tr><td>userName</td><td>string</td></tr></table>
        <pre><code>GET /api/v2/users</code></pre></main>"""),
        URL,
        SELECTORS,
    )

    assert "# Users" in markdown
    assert "| userName | string |" in markdown
    assert "GET /api/v2/users" in markdown
    for chrome in ("Product menu", "Groups", "Copyright", "track()", "cookies"):
        assert chrome not in markdown


def test_navigation_inside_the_main_element_is_removed() -> None:
    markdown = extract_main_content(
        _page(f"<article><nav>Docs / API / Users</nav><h1>Users</h1><p>{BODY_TEXT}</p></article>"),
        URL,
        SELECTORS,
    )

    assert "# Users" in markdown
    assert "Docs / API" not in markdown


def test_listing_of_several_articles_falls_back_to_the_page_body() -> None:
    cards = "".join(f"<article><h2>Guide {index}</h2><p>{BODY_TEXT}</p></article>" for index in range(3))

    markdown = extract_main_content(_page(f"<div>{cards}</div>"), URL, SELECTORS)

    assert all(f"Guide {index}" in markdown for index in range(3))
    assert "Product menu" not in markdown and "Copyright" not in markdown


def test_small_side_article_does_not_replace_the_page_content() -> None:
    main = f"<div><h1>Users</h1><p>{BODY_TEXT * 4}</p></div><article><p>{BODY_TEXT}</p></article>"

    markdown = extract_main_content(_page(main), URL, ["article"])

    assert "# Users" in markdown


def test_page_with_navigation_only_has_no_content() -> None:
    assert extract_main_content("<html><body><nav><a href='/a'>A</a></nav></body></html>", URL, SELECTORS) == ""
