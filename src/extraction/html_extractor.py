"""
HTML parsing and extraction module using BeautifulSoup4.
"""

from typing import List, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.utils.urls import canonicalize_url, is_same_registered_domain


class HTMLExtractionResult:
    """Container for extracted HTML page elements."""
    def __init__(
        self,
        title: str,
        visible_text: str,
        mailto_emails: List[str],
        internal_links: List[str],
        pdf_links: List[str],
        external_links: List[str] | None = None
    ):
        self.title = title
        self.visible_text = visible_text
        self.mailto_emails = mailto_emails
        self.internal_links = internal_links
        self.pdf_links = pdf_links
        self.external_links = external_links or []


def extract_from_html(
    html_content: str,
    base_url: str,
    target_registered_domain: str
) -> HTMLExtractionResult:
    """
    Parses HTML content to extract title, visible text, mailto links, internal page links, and PDF links.
    Strips noise elements (<script>, <style>, <noscript>, <svg>).
    """
    if not html_content or not isinstance(html_content, str):
        return HTMLExtractionResult("", "", [], [], [], [])

    soup = BeautifulSoup(html_content, "html.parser")

    # Extract title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Extract mailto links before decomposing elements
    mailto_emails: List[str] = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if href.lower().startswith("mailto:"):
            # Extract email portion, ignoring query params like mailto:info@example.com?subject=Hi
            raw_email = href[7:].split("?")[0].strip()
            if raw_email:
                mailto_emails.append(raw_email)

    # Decompose script, style, noscript, svg elements to leave clean visible text
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer"]):
        tag.decompose()

    # Extract clean visible text
    visible_text = soup.get_text(separator=" ", strip=True)

    # Extract links for crawler navigation & PDF discovery
    internal_links: Set[str] = set()
    pdf_links: Set[str] = set()
    external_links: Set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue

        absolute_url = urljoin(base_url, href)
        canonical_url = canonicalize_url(absolute_url)

        if not canonical_url:
            continue

        parsed = urlparse(canonical_url)
        if parsed.scheme not in ("http", "https"):
            continue

        on_target_domain = is_same_registered_domain(canonical_url, target_registered_domain)

        # Check if PDF link
        if parsed.path.lower().endswith(".pdf"):
            if on_target_domain:
                pdf_links.add(canonical_url)
        elif on_target_domain:
            internal_links.add(canonical_url)
        else:
            # Off-domain links are kept so external company presences
            # (careers portals, press rooms, social profiles) can be scanned
            external_links.add(canonical_url)

    return HTMLExtractionResult(
        title=title,
        visible_text=visible_text,
        mailto_emails=mailto_emails,
        internal_links=list(internal_links),
        pdf_links=list(pdf_links),
        external_links=list(external_links)
    )
