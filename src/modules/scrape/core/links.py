# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

from crawl4ai import CrawlResult  # type: ignore


def remove_anchor_links(urls: list[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    TODO: Temporary solution
    Remove anchor links from a list of URLs and return a mapping of original URLs to cleaned URLs.
    inputs:
        urls: list - list of URLs to clean
    outputs:
        tuple - (cleaned_urls, url_mapping) where:
            cleaned_urls: list - list of URLs with anchor links removed
            url_mapping: dict - mapping of original URLs to cleaned URLs for reference
    """
    cleaned_urls = []
    url_mapping = {}
    for url in urls:
        cleaned_url = url.split("#")[0]
        cleaned_urls.append(cleaned_url)
        url_mapping[url] = cleaned_url
    return cleaned_urls, url_mapping


def is_forbidden_url(url: str, forbidden_url_parts: list[str]) -> bool:
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


def clean_reference_list(reference_list):
    """
    Rework this function - works for now but not good for later.
    Only return the references which are either link or a relative reference that is not just '/'
    """
    return [
        link
        for link in reference_list
        if ("http" in link or "www" in link)
        or (link.startswith("/") and len(link) > 1)
        or (link.startswith("./") and len(link) > 2)
        or (link.startswith("../") and len(link) > 3)
    ]


def remove_trailing_slash(urls: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """
    Remove trailing slash from references if they exist, to avoid duplicates and inconsistencies in URL formatting.

    """
    new_urls = []
    map_of_links = {}
    for url in urls:
        if url.endswith("/"):
            map_of_links[url] = url[:-1]
            new_urls.append(url[:-1])
        else:
            new_urls.append(url)
    return new_urls, map_of_links


def relative_paths_to_absolute(reference_list: List[str], current_url: str) -> Tuple[List[str], Dict[str, str]]:
    """
    Scraped relative references are converted to absolute paths.
    The assumption here is that the current_url (i.e., the absolute path documentation we are currently scraping)
    is also the prefix of the relative path we are trying to reconstruct. This is a simple process of
    merging this current url (base) with the relative path scraped (using urljoin function).

    inputs:
        reference_list: list - list of scraped references (links) which may be relative or absolute
        current_url: str - the URL of the documentation currently being scraped, used as the base for converting relative paths
    outputs:
        tuple - (new_reference_list, map_of_links) where:
            new_reference_list: list - list of absolute URLs after conversion
            map_of_links: dict - mapping of original reference to its absolute URL for relative references
    """

    new_reference_list = []
    map_of_links = {}

    for link in reference_list:
        if not ("http" in link or "www" in link):
            map_of_links[link] = urljoin(current_url, urlparse(link).path)
            new_reference_list.append(urljoin(current_url, urlparse(link).path))
        else:
            new_reference_list.append(link)

    return new_reference_list, map_of_links


def extract_base_url(url: str):
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    return base_url


def get_links_for_documentation(scraperOutput: CrawlResult) -> list:
    """
    Extract and clean links from a CrawlResult object.
    inputs:
        scraperOutput: CrawlResult - the result object from the scraper
    outputs:
        list - cleaned list of absolute links
    """
    link_arr = []
    link_arr.extend([link["href"] for link in scraperOutput.links["internal"]])
    link_arr.extend([link["href"] for link in scraperOutput.links["external"]])
    link_arr_clean = clean_reference_list(link_arr)
    link_arr_abs, _ = relative_paths_to_absolute(link_arr_clean, scraperOutput.url)
    return link_arr_abs


def get_file_extension(url: str) -> str:
    """
    Extract file extension from URL.
    inputs:
        url: str - the URL string
    outputs:
        str - file extension (without dot), or empty string if none
    """
    parsed_url = urlparse(url)
    path = parsed_url.path
    if "." in path:
        return path.split(".")[-1]
    return ""
