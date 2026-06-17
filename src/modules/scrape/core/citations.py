# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

import logging
import re
from typing import Dict, List

from src.modules.scrape.schema import DocumentationReferences, ReferenceItem

logger = logging.getLogger(__name__)


def process_citations_markdown(
    markdown_references: str, text_with_citations: str, documentation_url: str
) -> DocumentationReferences:
    """
    Parse crawl4ai citation markdown and extract ReferenceItem objects
    inputs:
        markdown_references: str - the markdown content with references
        text_with_citations: str - the text with citations
        documentation_url: str - the URL of the documentation the markdown was generated from.
    outputs:
        DocumentationReferences - the DocumentationReferences object containing the extracted references and citation markdown
    """

    ref_section_match = re.search(r"##\s+References\s*\n(.*)", markdown_references, re.DOTALL)
    references_markdown = ref_section_match.group(0).strip() if ref_section_match else markdown_references.strip()
    ref_block = ref_section_match.group(1) if ref_section_match else markdown_references

    pattern = re.compile(r"⟨(\d+)⟩\s+(https?://\S+?):[^\S\r\n]?(.+)?")
    references: list[ReferenceItem] = []
    for match in pattern.finditer(ref_block):
        number, url = int(match.group(1)), match.group(2)
        description = match.group(3).strip() if match.group(3) else ""
        references.append(ReferenceItem(number=number, url=url, description=description))

    return DocumentationReferences(
        documentation_url=documentation_url,
        references=references,
        references_markdown=references_markdown,
        text_with_citations=text_with_citations,
    )


def remove_citations(documentation: DocumentationReferences, urls: List[str]) -> DocumentationReferences:
    """
    Remove citations from markdown content and return updated DocumentationReferences.
    inputs:
        documentation: DocumentationReferences - the original DocumentationReferences object containing the markdown with citations
        urls: list - list of URLs to remove from the markdown
    outputs:
        DocumentationReferences - the updated DocumentationReferences object with citations removed from the markdown
    """

    updated_markdown = documentation.text_with_citations
    updated_citations_markdown = documentation.references_markdown
    for url in urls:
        matching = [
            r.number for r in documentation.references if r.url == url or r.url == url + "/" or r.url + "/" == url
        ]
        if not matching:
            logger.warning("[Scrape:Citations] URL %s not found in references, skipping citation removal", url)
            continue
        if len(matching) > 1:
            logger.warning(
                "[Scrape:Citations] URL %s has multiple citations in the references, removing only the first one (number %s)",
                url,
                matching[0],
            )
        url_no = matching[0]
        updated_citations_markdown = re.sub(rf"⟨{url_no}⟩.*(?:\n|$)", "", updated_citations_markdown)
        updated_markdown = re.sub(rf"\⟨{url_no}\⟩", "", updated_markdown)
        documentation.references = [ref for ref in documentation.references if ref.url != url]

    return DocumentationReferences(
        documentation_url=documentation.documentation_url,
        references=documentation.references,
        references_markdown=updated_citations_markdown,
        text_with_citations=updated_markdown,
    )


def update_references(documentation: DocumentationReferences, url_mapping: Dict[str, str]) -> DocumentationReferences:
    """
    Update citations in markdown content based on a mapping of old URLs to new URLs.
    inputs:
        documentation: DocumentationReferences - the original DocumentationReferences object containing the markdown with citations
        url_mapping: dict - a mapping of old URLs to new URLs for updating the citations
    outputs:
        DocumentationReferences - the updated DocumentationReferences object with citations updated in the markdown content
    """
    updated_markdown = documentation.references_markdown
    for old_url, new_url in url_mapping.items():
        pattern = rf"(⟨\d+⟩\s+){re.escape(old_url)}(:\s+)"
        updated_markdown = re.sub(pattern, rf"\1{new_url}\2", updated_markdown)
        documentation.references = [
            ReferenceItem(
                number=ref.number, url=new_url if ref.url == old_url else ref.url, description=ref.description
            )
            for ref in documentation.references
        ]

    return DocumentationReferences(
        documentation_url=documentation.documentation_url,
        references=documentation.references,
        references_markdown=updated_markdown,
        text_with_citations=documentation.text_with_citations,
    )


def deduplicate_links(documentation_references: DocumentationReferences):
    """
    Remove duplicate links from a DocumentationReferences object.
    inputs:
        documentation_references: DocumentationReferences - the DocumentationReferences object to deduplicate
    outputs:
        Updates the DocumentationReference object in place
    """
    seen_urls = set()
    duplitcates: List[ReferenceItem] = []
    for ref in documentation_references.references:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
        else:
            duplitcates.append(ref)
    for dup in duplitcates:
        min_number = min(ref.number for ref in documentation_references.references if ref.url == dup.url)
        other_numbers = [
            ref.number for ref in documentation_references.references if ref.url == dup.url and ref.number != min_number
        ]
        for num in other_numbers:
            documentation_references.references_markdown = re.sub(
                rf"⟨{num}⟩.*(?:\n|$)", "", documentation_references.references_markdown
            )
            documentation_references.text_with_citations = re.sub(
                rf"⟨{num}⟩", f"<{min_number}>", documentation_references.text_with_citations
            )
            documentation_references.references = [
                ref for ref in documentation_references.references if ref.number != num
            ]
