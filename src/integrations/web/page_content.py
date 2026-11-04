# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Main-content extraction for scraped documentation pages.

crawl4ai's pruning filter scores blocks by text density and drops tables, code samples and short
reference rows, which are exactly what an endpoint or schema reference consists of. Instead, the
page's main element is located and converted to markdown as a whole, without navigation. Links are
still discovered from the full page, so removing the navigation here loses nothing reachable.
"""

from collections.abc import Sequence

from bs4 import BeautifulSoup, Tag
from crawl4ai import DefaultMarkdownGenerator  # type: ignore

# Never documentation text, wherever they appear: markup, navigation, dialogs and cookie-consent banners.
_NON_CONTENT_SELECTOR = (
    "script, style, noscript, template, svg, nav, [role=navigation], [role=dialog], [aria-modal=true], "
    "#onetrust-consent-sdk, #CybotCookiebotDialog"
)
# Page chrome removed when the main element cannot be located.
_PAGE_CHROME_SELECTOR = "header, footer, aside, [role=banner], [role=contentinfo], [role=complementary]"
_MIN_MAIN_CONTENT_CHARS = 200
# A matched element must hold this share of the page text; a smaller one is a teaser or a side box.
_MIN_MAIN_CONTENT_SHARE = 0.25
_MARKDOWN_OPTIONS = {"ignore_images": True, "skip_internal_links": True}


def extract_main_content(html: str, url: str, selectors: Sequence[str]) -> str:
    """Return the main content of a rendered page as markdown; empty when the page has no text.

    The first selector that matches exactly one outermost element holding most of the page text is
    used. A selector that matches several elements (a listing of cards) is skipped. Without a match the
    page body is used without header, footer and sidebars. CPU-bound: run it in a worker thread.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for element in soup.select(_NON_CONTENT_SELECTOR):
        element.decompose()
    page = soup.body or soup
    main = _select_main_element(page, selectors)
    if main is None:
        main = page
        for element in main.select(_PAGE_CHROME_SELECTOR):
            element.decompose()
    generator = DefaultMarkdownGenerator(options=_MARKDOWN_OPTIONS)
    return generator.generate_markdown(input_html=str(main), base_url=url, citations=False).raw_markdown.strip()


def _select_main_element(page: Tag, selectors: Sequence[str]) -> Tag | None:
    page_text_length = len(page.get_text(strip=True))
    for selector in selectors:
        matches = page.select(selector)
        match_ids = {id(match) for match in matches}
        outermost = [match for match in matches if not any(id(parent) in match_ids for parent in match.parents)]
        if len(outermost) != 1:
            continue
        text_length = len(outermost[0].get_text(strip=True))
        if text_length >= _MIN_MAIN_CONTENT_CHARS and text_length >= page_text_length * _MIN_MAIN_CONTENT_SHARE:
            return outermost[0]
    return None
