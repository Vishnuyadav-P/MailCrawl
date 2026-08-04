"""
URL Discovery and sitemap parsing module.
"""

from typing import List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup, FeatureNotFound

from src.utils.logging import logger
from src.utils.urls import canonicalize_url, get_url_priority, is_same_registered_domain
from src.validation.domain_validator import validate_url_ssrf

# A sitemap index may point at further indexes, but real ones nest only a level or
# two. The cap stops a malformed or hostile chain from recursing without end.
MAX_SITEMAP_DEPTH = 5


async def parse_sitemap(
    sitemap_url: str,
    client: httpx.AsyncClient,
    registered_domain: str,
    seen: Optional[Set[str]] = None,
    depth: int = 0
) -> Set[str]:
    """
    Fetches and recursively parses a sitemap XML file for valid target domain URLs.

    Args:
        seen: Sitemap URLs already fetched on this branch. Two sitemaps that reference
            each other would otherwise recurse until the interpreter's stack limit,
            issuing a request every time.
        depth: Current nesting level, capped at MAX_SITEMAP_DEPTH.
    """
    urls: Set[str] = set()

    if seen is None:
        seen = set()

    canonical_self = canonicalize_url(sitemap_url) or sitemap_url
    if canonical_self in seen:
        return urls
    seen.add(canonical_self)

    if depth > MAX_SITEMAP_DEPTH:
        logger.warning(
            f"Sitemap nesting deeper than {MAX_SITEMAP_DEPTH} levels at '{sitemap_url}'; "
            f"stopping this branch."
        )
        return urls

    is_safe, reason = validate_url_ssrf(sitemap_url)
    if not is_safe:
        logger.warning(f"Skipping sitemap {sitemap_url}: {reason}")
        return urls

    try:
        resp = await client.get(sitemap_url, timeout=10.0, follow_redirects=True)
        if resp.status_code != 200:
            return urls

        # The 'xml' feature needs lxml; without it BeautifulSoup raises and every
        # sitemap silently yields nothing, so fall back to the bundled HTML parser.
        try:
            soup = BeautifulSoup(resp.content, "xml")
        except FeatureNotFound:
            logger.warning(
                "lxml is not installed, so sitemaps are parsed with the slower HTML "
                "parser. Install lxml for correct XML namespace handling."
            )
            soup = BeautifulSoup(resp.content, "html.parser")

        loc_tags = soup.find_all("loc")

        for tag in loc_tags:
            if tag.string:
                loc_url = tag.string.strip()
                canonical = canonicalize_url(loc_url)

                if canonical.endswith(".xml") or "sitemap" in canonical.lower():
                    # Nested sitemap index; `seen` is shared so a cycle terminates
                    sub_urls = await parse_sitemap(
                        loc_url, client, registered_domain, seen=seen, depth=depth + 1
                    )
                    urls.update(sub_urls)
                elif is_same_registered_domain(canonical, registered_domain):
                    urls.add(canonical)

    except Exception as exc:
        logger.warning(f"Error parsing sitemap at '{sitemap_url}': {exc}")

    return urls


async def discover_seed_urls(
    base_urls: List[str],
    registered_domain: str,
    client: httpx.AsyncClient,
    known_sitemaps: List[str]
) -> List[Tuple[str, int, int]]:
    """
    Discovers initial candidate URLs from the sitemaps and homepage of every site
    published under the registered domain.

    Args:
        base_urls: Every reachable origin for the domain, e.g.
            ["https://example.com", "https://careers.example.com"]

    Returns:
        List[Tuple[str, int, int]]: List of (url, depth, priority_score) sorted by priority descending.
    """
    discovered_set: Set[str] = set()

    # Candidate sitemap locations across every site on the domain
    sitemap_candidates = set(known_sitemaps)

    for base_url in base_urls:
        # Always include each site's homepage
        discovered_set.add(canonicalize_url(base_url))

        origin = base_url.rstrip("/")
        sitemap_candidates.add(f"{origin}/sitemap.xml")
        sitemap_candidates.add(f"{origin}/sitemap_index.xml")

    for s_url in sitemap_candidates:
        parsed_urls = await parse_sitemap(s_url, client, registered_domain)
        discovered_set.update(parsed_urls)

    # Prioritize and format into list of tuples
    results: List[Tuple[str, int, int]] = []
    for url in discovered_set:
        priority = get_url_priority(url)
        results.append((url, 0, priority))  # (url, depth, priority_score)

    # Sort descending by priority score
    results.sort(key=lambda item: item[2], reverse=True)
    logger.info(
        f"URL discovery completed for '{registered_domain}' across {len(base_urls)} site(s). "
        f"Total seed URLs discovered: {len(results)}"
    )

    return results
