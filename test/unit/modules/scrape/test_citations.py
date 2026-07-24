# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from src.common.documentation import DocumentationReferences, ReferenceItem
from src.modules.scrape.core.citations import (
    deduplicate_links,
    process_citations_markdown,
    remove_citations,
    update_references,
)


def test_reference_url_with_port_is_not_truncated():
    """A reference URL containing a port (colon) must be parsed in full.

    Regression: the non-greedy '(https?://\\S+?):' group stopped at the first
    colon, truncating the URL at the port and leaking the rest into the
    description.
    """
    markdown = "## References\n⟨1⟩ https://api.example.com:8443/v2/users: Users API\n"

    result = process_citations_markdown(markdown, "text ⟨1⟩", "https://api.example.com")

    assert len(result.references) == 1
    assert result.references[0].url == "https://api.example.com:8443/v2/users"
    assert result.references[0].description == "Users API"


def test_remove_citations_removes_reference_on_slash_variant_match():
    """When a citation is matched slash-tolerantly, its ReferenceItem must also be removed.

    Regression: the number was matched with a slash-tolerant predicate but the
    ReferenceItem was deleted with an exact url comparison, leaving an orphaned
    reference whose citation no longer exists in the text.
    """
    documentation = DocumentationReferences(
        documentation_url="https://x/doc",
        references=[ReferenceItem(url="https://x/a/", description="d", number=1)],
        references_markdown="⟨1⟩ https://x/a/: d",
        text_with_citations="foo ⟨1⟩ bar",
    )

    updated = remove_citations(documentation, ["https://x/a"])

    assert "⟨1⟩" not in updated.text_with_citations
    assert updated.references == []


def test_update_references_handles_backslash_in_new_url():
    """A new URL containing backslash sequences must be inserted literally.

    Regression: the new URL was interpolated into the re.sub replacement
    template, where sequences like '\\x' raised 're.error: bad escape' and
    killed the whole scrape job. Such URLs occur as junk link candidates
    extracted from rendered source files (e.g. JS with '\\x1b' escapes).
    """
    new_url = "https://github.com/jelhub/scimgateway/blob/master/lib/junk\\x1b[0m"
    documentation = DocumentationReferences(
        documentation_url="https://x/doc",
        references=[ReferenceItem(url="junk", description="d", number=1)],
        references_markdown="⟨1⟩ junk: d",
        text_with_citations="foo ⟨1⟩ bar",
    )

    updated = update_references(documentation, {"junk": new_url})

    assert updated.references_markdown == f"⟨1⟩ {new_url}: d"
    assert updated.references[0].url == new_url


def test_deduplicate_links_uses_canonical_marker_form():
    """Duplicate citation markers must be rewritten to the canonical ⟨n⟩ form.

    Regression: duplicates were rewritten to ASCII '<n>', which downstream
    citation removal (matching only ⟨n⟩) could never strip, leaving orphaned
    '<n>' literals in the content fed to the LLM.
    """
    documentation = DocumentationReferences(
        documentation_url="https://x/doc",
        references=[
            ReferenceItem(url="https://x/a", description="d", number=1),
            ReferenceItem(url="https://x/a", description="d", number=2),
        ],
        references_markdown="⟨1⟩ https://x/a: d\n⟨2⟩ https://x/a: d",
        text_with_citations="see ⟨1⟩ and ⟨2⟩",
    )

    deduplicate_links(documentation)

    assert "<1>" not in documentation.text_with_citations
    assert documentation.text_with_citations.count("⟨1⟩") == 2
    assert [ref.number for ref in documentation.references] == [1]

    # The deduplicated markers must be strippable by the normal removal path.
    cleaned = remove_citations(documentation, ["https://x/a"])
    assert "1" not in cleaned.text_with_citations
