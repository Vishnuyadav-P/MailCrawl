"""
URL normalization, domain extraction, canonicalization, and priority scoring utilities.
"""

import re
from functools import lru_cache
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import tldextract

from src.extraction.language import priority_keywords

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "msclkid", "ref", "source"
}

VERY_HIGH_PRIORITY_KEYWORDS = [
    "contact", "contact-us", "contactus", "team", "people", "leadership",
    "management", "staff", "directory", "about-us", "aboutus"
]

HIGH_PRIORITY_KEYWORDS = [
    "about", "company", "offices", "locations", "board", "executives", "members"
]

MEDIUM_PRIORITY_KEYWORDS = [
    "careers", "jobs", "support", "sales", "partners", "press", "media", "investors"
]

LOW_PRIORITY_KEYWORDS = [
    "blog", "news", "privacy", "terms", "faq", "legal", "help"
]

# One compiled alternation per tier instead of a keyword-at-a-time substring scan.
# get_url_priority runs on every link on every page, so this is one pass over the
# path rather than up to 33.
_PRIORITY_TIERS = tuple(
    (re.compile("|".join(re.escape(kw) for kw in keywords)), score)
    for keywords, score in (
        (VERY_HIGH_PRIORITY_KEYWORDS, 90),
        (HIGH_PRIORITY_KEYWORDS, 75),
        (MEDIUM_PRIORITY_KEYWORDS, 50),
        (LOW_PRIORITY_KEYWORDS, 25),
    )
)

# Caches for the three functions the crawler calls most. A site's navigation repeats
# the same hrefs on every page, so a crawl of n pages with an m-link nav asks these
# roughly n*m times about m distinct URLs. Sizes are capped so a large crawl cannot
# grow them without bound.
_URL_CACHE_SIZE = 50_000
_DOMAIN_CACHE_SIZE = 20_000


def normalize_domain_input(raw_input: str) -> tuple[str, str]:
    """
    Normalizes a domain input (e.g., 'example.com', 'https://www.example.com/about/')
    into (registered_domain, base_url).

    Returns:
        tuple[str, str]: (registered_domain, base_url) e.g., ("example.com", "https://example.com")

    Raises:
        ValueError: If domain input is invalid or uses an disallowed scheme.
    """
    if not raw_input or not isinstance(raw_input, str):
        raise ValueError("Domain input cannot be empty.")

    cleaned = raw_input.strip()

    # Prepend scheme if missing
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        cleaned = "https://" + cleaned

    parsed = urlparse(cleaned)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Disallowed scheme '{parsed.scheme}'. Only HTTP/HTTPS are supported.")

    host = parsed.hostname or parsed.netloc or parsed.path
    # DNS and HTTP use ASCII IDNA; accept Unicode input at the API boundary.
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"Invalid internationalized domain: '{raw_input}'") from exc
    extracted = tldextract.extract(ascii_host)
    if not extracted.domain or not extracted.suffix:
        raise ValueError(f"Invalid domain input: '{raw_input}'")

    registered_domain = f"{extracted.domain}.{extracted.suffix}".lower()
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    base_url = f"{scheme}://{registered_domain}"

    return registered_domain, base_url


@lru_cache(maxsize=_DOMAIN_CACHE_SIZE)
def is_same_registered_domain(url_or_domain: str, target_registered_domain: str) -> bool:
    """Checks if a URL or email domain belongs to the target registered domain."""
    if not url_or_domain or not target_registered_domain:
        return False

    target = _idna_host(target_registered_domain)

    if "@" in url_or_domain:
        domain_part = _idna_host(url_or_domain.split("@")[-1].strip())
        extracted = tldextract.extract(domain_part)
        if not extracted.domain or not extracted.suffix:
            return False
        return f"{extracted.domain}.{extracted.suffix}".lower() == target

    parsed = urlparse(url_or_domain if "://" in url_or_domain else f"https://{url_or_domain}")
    extracted = tldextract.extract(_idna_host(parsed.hostname or parsed.netloc))
    if not extracted.domain or not extracted.suffix:
        return False

    return f"{extracted.domain}.{extracted.suffix}".lower() == target


@lru_cache(maxsize=_URL_CACHE_SIZE)
def canonicalize_url(url: str) -> str:
    """
    Normalizes a URL by:
    - Removing hash fragments
    - Removing common tracking query parameters
    - Standardizing trailing slashes (stripping trailing slash for non-root paths)
    - Lowercasing scheme and hostname
    """
    if not url:
        return ""

    # Split fragment out explicitly before parsing query params
    clean_url_no_frag = url.split("#")[0]

    parsed = urlparse(clean_url_no_frag)
    if parsed.scheme not in ("http", "https"):
        return url

    hostname = _idna_host(parsed.hostname or "")
    netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
    path = parsed.path

    # Clean query parameters
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    filtered_params = {
        k: v for k, v in query_params.items()
        if k.lower() not in TRACKING_PARAMS
    }
    new_query = urlencode(filtered_params, doseq=True)

    # Normalize path trailing slash
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Reconstruct URL
    clean_parsed = urlunparse((
        parsed.scheme.lower(),
        netloc,
        path,
        "",  # params
        new_query,
        ""   # fragment
    ))

    return clean_parsed



@lru_cache(maxsize=_URL_CACHE_SIZE)
def get_url_priority(url: str) -> int:
    """
    Assigns a priority score (0 to 100) to a URL based on path keyword matching.
    Higher score means higher priority in the crawl queue.
    """
    if not url:
        return 0

    path_lower = urlparse(url).path.lower()

    if path_lower in ("", "/"):
        return 100  # Homepage is maximum priority

    for pattern, score in _PRIORITY_TIERS:
        if pattern.search(path_lower):
            return score

    return 40  # Default score for generic pages


def _idna_host(host: str) -> str:
    """Return a lowercase ASCII hostname, safely encoding Unicode labels."""
    host = (host or "").strip().rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


# Translated navigation labels influence the queue but never exclude a page.
_localized = tuple(re.escape(word) for word in priority_keywords())
if _localized:
    _PRIORITY_TIERS = ((re.compile("|".join(_localized), re.IGNORECASE), 70),) + _PRIORITY_TIERS
