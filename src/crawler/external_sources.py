"""
Off-domain source scanning.

A company's contactable addresses are frequently published somewhere other than its
own website — an applicant-tracking careers portal, a press room, a developer profile.
This module picks those out of the off-domain links found while crawling, plus any
URLs the operator supplies, and scans the ones that are publicly readable.

A note on social networks: LinkedIn, Facebook, Instagram and Glassdoor put nearly all
content behind an authentication wall and return HTTP 999/403 to unauthenticated
clients. Those URLs are attempted like any other and the auth wall is reported as a
scan error. This module deliberately contains no login handling, user-agent rotation,
or proxy rotation, so pages that require an account stay unreadable.
"""

import asyncio
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import httpx

from src.extraction.context_extractor import extract_surrounding_context
from src.extraction.email_extractor import extract_emails_from_text, process_mailto_emails
from src.extraction.html_extractor import extract_from_html
from src.models.email import EmailOccurrence
from src.models.scan import ScanError
from src.utils.config import Config
from src.utils.logging import logger
from src.utils.urls import canonicalize_url
from src.validation.domain_validator import validate_url_ssrf

# Platforms that publish company contact/careers content without an account
OPEN_EXTERNAL_PLATFORMS: Dict[str, str] = {
    "greenhouse.io": "Careers portal",
    "lever.co": "Careers portal",
    "workable.com": "Careers portal",
    "smartrecruiters.com": "Careers portal",
    "myworkdayjobs.com": "Careers portal",
    "bamboohr.com": "Careers portal",
    "jobvite.com": "Careers portal",
    "icims.com": "Careers portal",
    "taleo.net": "Careers portal",
    "recruitee.com": "Careers portal",
    "ashbyhq.com": "Careers portal",
    "successfactors.com": "Careers portal",
    "crunchbase.com": "Company profile",
    "github.com": "Developer profile",
    "medium.com": "Company blog",
    "prnewswire.com": "Press release",
    "businesswire.com": "Press release",
    "globenewswire.com": "Press release",
    "youtube.com": "Social profile",
}

# Platforms that require an account; attempted, but expected to refuse
AUTH_WALLED_PLATFORMS: Dict[str, str] = {
    "linkedin.com": "LinkedIn",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "glassdoor.com": "Glassdoor",
    "x.com": "X (Twitter)",
    "twitter.com": "Twitter",
}

ALL_EXTERNAL_PLATFORMS = {**OPEN_EXTERNAL_PLATFORMS, **AUTH_WALLED_PLATFORMS}

# Signals that a response is a login prompt rather than the requested content
AUTH_WALL_STATUS_CODES = {401, 403, 429, 999}
AUTH_WALL_URL_MARKERS = ("/login", "/signup", "/authwall", "/checkpoint", "/uas/login")


def classify_external_url(url: str) -> Optional[Tuple[str, str, bool]]:
    """
    Identifies whether a URL points at a known company-presence platform.

    Returns:
        Optional[Tuple[str, str, bool]]: (platform_host, label, requires_auth), or None.
    """
    host = urlparse(url).netloc.lower()
    if not host:
        return None

    for platform_host, label in ALL_EXTERNAL_PLATFORMS.items():
        if host == platform_host or host.endswith(f".{platform_host}"):
            return platform_host, label, platform_host in AUTH_WALLED_PLATFORMS

    return None


def select_external_sources(
    candidate_urls: Set[str],
    extra_urls: Optional[List[str]] = None,
    max_sources: Optional[int] = None
) -> List[str]:
    """
    Filters off-domain links down to recognised company-presence pages.

    Operator-supplied URLs are always kept and always come first — they are an
    explicit instruction, not a guess.
    """
    selected: List[str] = []
    seen: Set[str] = set()

    for raw_url in (extra_urls or []):
        url = canonicalize_url(raw_url.strip())
        if url and url not in seen:
            seen.add(url)
            selected.append(url)

    for raw_url in sorted(candidate_urls):
        url = canonicalize_url(raw_url)
        if not url or url in seen:
            continue
        if classify_external_url(url):
            seen.add(url)
            selected.append(url)

    limit = Config.MAX_EXTERNAL_SOURCES if max_sources is None else max_sources
    return selected[:limit] if limit and limit > 0 else selected


def _looks_like_auth_wall(resp: httpx.Response) -> bool:
    """Detects a login prompt served in place of the requested page."""
    if resp.status_code in AUTH_WALL_STATUS_CODES:
        return True

    final_url = str(resp.url).lower()
    return any(marker in final_url for marker in AUTH_WALL_URL_MARKERS)


async def _scan_one_source(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    registered_domain: str,
    timeout: float
) -> Tuple[List[EmailOccurrence], Optional[ScanError]]:
    """Fetches a single external page and extracts any emails it publishes."""
    classification = classify_external_url(url)
    label = classification[1] if classification else "External page"

    is_safe, reason = await asyncio.to_thread(validate_url_ssrf, url)
    if not is_safe:
        return [], ScanError(url=url, error_type="SSRF Blocked", message=reason)

    async with semaphore:
        try:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
        except Exception as exc:
            return [], ScanError(
                url=url,
                error_type="External Source Error",
                message=f"{label}: {exc}"
            )

    if _looks_like_auth_wall(resp):
        platform = classification[0] if classification else urlparse(url).netloc
        return [], ScanError(
            url=url,
            status_code=resp.status_code,
            error_type="Authentication Required",
            message=(
                f"{label} ({platform}) served a login wall (HTTP {resp.status_code}). "
                f"Its content is not publicly readable, and this scanner does not "
                f"bypass authentication."
            )
        )

    if resp.status_code != 200:
        return [], ScanError(
            url=url,
            status_code=resp.status_code,
            error_type="HTTP Error",
            message=f"{label}: HTTP status code {resp.status_code}"
        )

    content_type = resp.headers.get("content-type", "").lower()
    if "html" not in content_type and "text" not in content_type:
        return [], None

    extracted = extract_from_html(resp.text, url, registered_domain)
    source_title = f"[{label}] {extracted.title or urlparse(url).netloc}"

    occurrences: List[EmailOccurrence] = []
    occurrences.extend(process_mailto_emails(extracted.mailto_emails, url, source_title))
    occurrences.extend(extract_emails_from_text(extracted.visible_text, url, source_title))

    for occ in occurrences:
        occ.source_type = "external"
        occ.context = extract_surrounding_context(extracted.visible_text, occ.email)

    return occurrences, None


async def scan_external_sources(
    urls: List[str],
    client: httpx.AsyncClient,
    registered_domain: str,
    timeout: float = 15.0,
    concurrency: int = 5,
    on_progress: Optional[callable] = None
) -> Tuple[List[EmailOccurrence], List[ScanError]]:
    """
    Scans off-domain company-presence pages for published email addresses.

    Returns:
        Tuple[List[EmailOccurrence], List[ScanError]]: (occurrences, errors)
    """
    if not urls:
        return [], []

    semaphore = asyncio.Semaphore(concurrency)
    all_occurrences: List[EmailOccurrence] = []
    all_errors: List[ScanError] = []

    async def _run_one(idx: int, url: str) -> Tuple[List[EmailOccurrence], Optional[ScanError]]:
        if on_progress:
            on_progress(idx, len(urls), url)
        return await _scan_one_source(url, client, semaphore, registered_domain, timeout)

    tasks = [_run_one(idx, url) for idx, url in enumerate(urls, start=1)]
    results = await asyncio.gather(*tasks)

    for occurrences, error in results:
        all_occurrences.extend(occurrences)
        if error:
            all_errors.append(error)

    logger.info(
        f"External source scan finished: {len(urls)} page(s) attempted, "
        f"{len(all_occurrences)} email occurrence(s) found, {len(all_errors)} blocked or failed."
    )

    return all_occurrences, all_errors
