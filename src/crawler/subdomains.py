"""
Subdomain enumeration, so every site published under the registered domain is
crawled rather than only the apex host.

Candidate hosts come from two sources:
  1. crt.sh certificate transparency logs - every name a public TLS certificate was
     issued for, which is the most complete free view of a company's hostnames.
  2. A fixed list of hosts that commonly carry contactable content, as a fallback
     for when crt.sh is unavailable or rate limited.

Candidates are then probed over HTTP and only reachable origins are returned.
"""

import asyncio
import json
from typing import List, Optional, Set
from urllib.parse import urlparse

import httpx

from src.utils.config import Config
from src.utils.logging import logger
from src.utils.urls import is_same_registered_domain
from src.validation.domain_validator import validate_url_ssrf

CRT_SH_URL = "https://crt.sh/"
CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"

# crt.sh is a free community service: queries regularly take 30s+ and it answers
# with a transient 502/503 under load, so a single attempt is not a fair test.
CRT_SH_ATTEMPTS = 3
CRT_SH_RETRY_BACKOFF_SECONDS = 3.0


def is_likely_public_host(host: str, registered_domain: str) -> bool:
    """
    Judges whether a hostname is a public-facing site rather than internal plumbing.

    Certificate logs are dominated by dev/QA apps and mail endpoints that never serve
    a contact page, so probing them wastes the crawl budget.
    """
    if not Config.SKIP_NON_PUBLIC_SUBDOMAINS:
        return True

    if host == registered_domain or host == f"www.{registered_domain}":
        return True

    prefix = host[: -len(f".{registered_domain}")] if host.endswith(f".{registered_domain}") else host

    # Compare whole dot/dash-delimited labels so 'news' is not rejected by the 'np'
    # marker and 'press' is not rejected by the 'pr' marker.
    labels = {part for chunk in prefix.split(".") for part in chunk.split("-") if part}

    return not labels.intersection(Config.NON_PUBLIC_SUBDOMAIN_MARKERS)


def _clean_certificate_name(raw_name: str, registered_domain: str) -> Optional[str]:
    """Normalizes one certificate name entry into a usable hostname."""
    host = raw_name.strip().lower().lstrip("*.").strip(".")

    if not host or " " in host or "@" in host:
        return None

    # Certificates routinely cover unrelated domains on shared infrastructure
    if host != registered_domain and not host.endswith(f".{registered_domain}"):
        return None

    return host


async def fetch_crt_sh_subdomains(
    registered_domain: str,
    client: httpx.AsyncClient
) -> Set[str]:
    """Queries crt.sh for every hostname seen on a certificate for the domain."""
    hosts: Set[str] = set()
    last_problem = "no response"

    for attempt in range(1, CRT_SH_ATTEMPTS + 1):
        try:
            resp = await client.get(
                CRT_SH_URL,
                params={"q": f"%.{registered_domain}", "output": "json"},
                timeout=Config.CRT_SH_TIMEOUT_SECONDS,
                follow_redirects=True
            )
        except Exception as exc:
            last_problem = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 200:
                break
            last_problem = f"HTTP {resp.status_code}"

        if attempt < CRT_SH_ATTEMPTS:
            await asyncio.sleep(CRT_SH_RETRY_BACKOFF_SECONDS * attempt)
    else:
        logger.warning(
            f"crt.sh lookup for '{registered_domain}' failed after {CRT_SH_ATTEMPTS} "
            f"attempts ({last_problem}); falling back to common subdomain probing."
        )
        return hosts

    try:
        # crt.sh occasionally returns newline-delimited JSON objects instead of an array
        try:
            entries = resp.json()
        except json.JSONDecodeError:
            entries = [json.loads(line) for line in resp.text.splitlines() if line.strip()]

        for entry in entries:
            # 'name_value' may hold several newline-separated SAN entries
            for raw_name in str(entry.get("name_value", "")).splitlines():
                host = _clean_certificate_name(raw_name, registered_domain)
                if host:
                    hosts.add(host)

            common_name = _clean_certificate_name(
                str(entry.get("common_name", "")), registered_domain
            )
            if common_name:
                hosts.add(common_name)

        logger.info(f"crt.sh returned {len(hosts)} hostname(s) for '{registered_domain}'.")

    except Exception as exc:
        logger.warning(
            f"Could not parse the crt.sh response for '{registered_domain}': {exc}; "
            f"falling back to common subdomain probing."
        )

    return hosts


async def fetch_certspotter_subdomains(
    registered_domain: str,
    client: httpx.AsyncClient
) -> Set[str]:
    """
    Queries Cert Spotter's certificate log, used alongside crt.sh.

    It answers in about a second where crt.sh often takes 30s or fails outright, but
    its keyless tier is rate limited, so neither source is relied on alone.
    """
    hosts: Set[str] = set()

    try:
        resp = await client.get(
            CERTSPOTTER_URL,
            params={
                "domain": registered_domain,
                "include_subdomains": "true",
                "expand": "dns_names",
            },
            timeout=Config.CRT_SH_TIMEOUT_SECONDS,
            follow_redirects=True
        )

        if resp.status_code == 429:
            logger.info("Cert Spotter rate limit reached; relying on crt.sh for this scan.")
            return hosts

        if resp.status_code != 200:
            logger.info(f"Cert Spotter returned HTTP {resp.status_code} for '{registered_domain}'.")
            return hosts

        for entry in resp.json():
            for raw_name in entry.get("dns_names", []):
                host = _clean_certificate_name(str(raw_name), registered_domain)
                if host:
                    hosts.add(host)

        logger.info(f"Cert Spotter returned {len(hosts)} hostname(s) for '{registered_domain}'.")

    except Exception as exc:
        logger.info(f"Cert Spotter lookup failed for '{registered_domain}': {exc}")

    return hosts


async def _probe_host(
    host: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    registered_domain: str,
    timeout: float
) -> Optional[str]:
    """Returns the reachable origin for a host, or None when it does not serve HTTP."""
    is_safe, reason = await asyncio.to_thread(validate_url_ssrf, f"https://{host}")
    if not is_safe:
        logger.info(f"Skipping subdomain '{host}': {reason}")
        return None

    async with semaphore:
        for candidate in (f"https://{host}", f"http://{host}"):
            try:
                resp = await client.get(candidate, timeout=timeout, follow_redirects=True)
            except Exception:
                continue

            if resp.status_code >= 400:
                continue

            # A host that redirects off the registered domain is a parked or vanity
            # name rather than a site of its own, so it is not worth seeding.
            final_url = str(resp.url)
            if not is_same_registered_domain(final_url, registered_domain):
                return None

            parsed = urlparse(final_url)
            return f"{parsed.scheme}://{parsed.netloc}"

    return None


async def discover_subdomain_origins(
    registered_domain: str,
    client: httpx.AsyncClient,
    timeout: float = 10.0,
    concurrency: int = 20
) -> List[str]:
    """
    Enumerates and probes every host under the registered domain.

    Returns:
        List[str]: Reachable origins (e.g. ["https://example.com", "https://careers.example.com"])
    """
    candidates: Set[str] = {registered_domain, f"www.{registered_domain}"}

    if Config.SUBDOMAIN_DISCOVERY_ENABLED:
        # Both logs are queried together: each one alone has visible gaps and
        # outages, and their union is the closest thing to a full host list.
        crt_hosts, certspotter_hosts = await asyncio.gather(
            fetch_crt_sh_subdomains(registered_domain, client),
            fetch_certspotter_subdomains(registered_domain, client)
        )
        candidates |= crt_hosts | certspotter_hosts

    candidates |= {
        f"{prefix}.{registered_domain}"
        for prefix in Config.COMMON_SUBDOMAIN_PREFIXES
    }

    total_candidates = len(candidates)
    candidates = {
        host for host in candidates
        if is_likely_public_host(host, registered_domain)
    }
    skipped = total_candidates - len(candidates)

    # Shortest names first: apex and one-label hosts carry the most useful content,
    # so they are probed first even though every candidate is probed.
    ordered = sorted(candidates, key=lambda h: (h.count("."), len(h)))
    if Config.MAX_SUBDOMAINS > 0:
        ordered = ordered[:Config.MAX_SUBDOMAINS]

    if skipped:
        logger.info(
            f"Skipped {skipped} non-public host(s) for '{registered_domain}' "
            f"(dev/QA/mail endpoints). Set SKIP_NON_PUBLIC_SUBDOMAINS=false to include them."
        )

    semaphore = asyncio.Semaphore(concurrency)
    probes = [
        _probe_host(host, client, semaphore, registered_domain, timeout)
        for host in ordered
    ]
    results = await asyncio.gather(*probes)

    origins: List[str] = []
    seen: Set[str] = set()
    for origin in results:
        if origin and origin not in seen:
            seen.add(origin)
            origins.append(origin)

    # The apex must always be crawled even if the probe failed transiently
    apex = f"https://{registered_domain}"
    if apex not in seen:
        origins.insert(0, apex)

    logger.info(
        f"Subdomain discovery for '{registered_domain}': probed {len(ordered)} candidate host(s), "
        f"{len(origins)} reachable site(s)."
    )

    return origins
