"""
Standards-based contact discovery.

Page crawling only finds addresses somebody chose to publish in HTML. These sources
are machine-readable records that a domain owner maintains deliberately, and they
routinely expose working addresses that appear nowhere on the website:

  * security.txt (RFC 9116) - 'Contact: mailto:...' for the security team
  * DMARC / SOA DNS records  - report and hostmaster mailboxes
  * RDAP (the WHOIS successor) - registrant, admin, tech and abuse contacts

All three are public, unauthenticated, and designed to be read by automated clients.
"""

import asyncio
import re
from typing import Dict, List, Optional, Set, Tuple

import dns.resolver
import httpx

from src.models.email import EmailOccurrence
from src.models.scan import ScanError
from src.utils.logging import logger
from src.validation.email_validator import (
    is_false_positive_email,
    is_valid_email_syntax,
    normalize_email_address,
)

RDAP_BOOTSTRAP_URL = "https://rdap.org/domain/"

SECURITY_TXT_PATHS = ("/.well-known/security.txt", "/security.txt")

# 'Contact: mailto:security@example.com' — the field may also hold a URL, which is skipped
SECURITY_TXT_CONTACT = re.compile(r"^contact:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# DMARC aggregate/forensic report destinations: 'rua=mailto:a@x.com,mailto:b@x.com'
DMARC_REPORT_TAG = re.compile(r"\b(rua|ruf)\s*=\s*([^;]+)", re.IGNORECASE)

ANY_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _build_occurrence(
    raw_email: str,
    source_url: str,
    source_title: str,
    source_type: str,
    context: str = ""
) -> Optional[EmailOccurrence]:
    """Validates a raw address and wraps it as an occurrence, or returns None."""
    email = normalize_email_address(raw_email)
    if not email or not is_valid_email_syntax(email) or is_false_positive_email(email):
        return None

    return EmailOccurrence(
        email=email,
        source_url=source_url,
        source_title=source_title,
        source_type=source_type,
        context=context
    )


async def fetch_security_txt_emails(
    origins: List[str],
    client: httpx.AsyncClient,
    timeout: float = 10.0
) -> List[EmailOccurrence]:
    """Reads RFC 9116 security.txt from every site on the domain."""
    occurrences: List[EmailOccurrence] = []
    seen: Set[str] = set()

    async def read_one(url: str) -> Optional[str]:
        try:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
        except Exception:
            return None

        if resp.status_code != 200:
            return None

        # A site that serves its HTML 200 for unknown paths would otherwise be
        # mined for every address on its error page.
        if "text/plain" not in resp.headers.get("content-type", "").lower():
            return None

        return resp.text

    targets = [
        f"{origin.rstrip('/')}{path}"
        for origin in origins
        for path in SECURITY_TXT_PATHS
    ]

    bodies = await asyncio.gather(*[read_one(url) for url in targets])

    # gather() preserves order and length, so a mismatch would be a real bug.
    for url, body in zip(targets, bodies, strict=True):
        if not body:
            continue

        for contact_value in SECURITY_TXT_CONTACT.findall(body):
            value = contact_value.strip()
            # The Contact field also accepts https:// and tel:, which carry no address
            if not value.lower().startswith("mailto:") and "@" not in value:
                continue

            occ = _build_occurrence(
                raw_email=value,
                source_url=url,
                source_title="[security.txt] Security contact",
                source_type="security.txt",
                context=f"Declared as a security contact in {url}"
            )
            if occ and occ.email not in seen:
                seen.add(occ.email)
                occurrences.append(occ)

    if occurrences:
        logger.info(f"security.txt yielded {len(occurrences)} address(es).")

    return occurrences


def _soa_rname_to_email(rname: str) -> str:
    """Converts an SOA RNAME ('hostmaster.example.com.') into 'hostmaster@example.com'."""
    cleaned = rname.strip().rstrip(".")

    # The local part's dots are escaped in an RNAME, so the first unescaped dot splits it
    match = re.search(r"(?<!\\)\.", cleaned)
    if not match:
        return ""

    local = cleaned[: match.start()].replace("\\.", ".")
    domain = cleaned[match.end():]
    return f"{local}@{domain}" if local and domain else ""


def _lookup_dns_contacts_blocking(registered_domain: str) -> List[EmailOccurrence]:
    """Reads contact addresses out of the domain's own DNS records."""
    occurrences: List[EmailOccurrence] = []
    seen: Set[str] = set()

    def add(raw: str, source_type: str, title: str, context: str) -> None:
        occ = _build_occurrence(raw, f"dns://{registered_domain}", title, source_type, context)
        if occ and occ.email not in seen:
            seen.add(occ.email)
            occurrences.append(occ)

    # SOA responsible-person mailbox
    try:
        for record in dns.resolver.resolve(registered_domain, "SOA", lifetime=8.0):
            email = _soa_rname_to_email(str(record.rname))
            if email:
                add(
                    email, "dns", "[DNS] SOA responsible person",
                    f"Responsible-person mailbox in the SOA record for {registered_domain}"
                )
    except Exception as exc:
        logger.info(f"No SOA contact for '{registered_domain}': {exc}")

    # DMARC aggregate and forensic report mailboxes
    try:
        for record in dns.resolver.resolve(f"_dmarc.{registered_domain}", "TXT", lifetime=8.0):
            txt = b"".join(record.strings).decode("utf-8", errors="ignore")
            for tag, value in DMARC_REPORT_TAG.findall(txt):
                for destination in value.split(","):
                    add(
                        destination.strip(), "dns",
                        f"[DNS] DMARC {tag} report address",
                        f"DMARC {tag.lower()} report destination for {registered_domain}"
                    )
    except Exception as exc:
        logger.info(f"No DMARC record for '{registered_domain}': {exc}")

    # Apex TXT records occasionally carry a plain contact address
    try:
        for record in dns.resolver.resolve(registered_domain, "TXT", lifetime=8.0):
            txt = b"".join(record.strings).decode("utf-8", errors="ignore")
            for found in ANY_EMAIL.findall(txt):
                add(
                    found, "dns", "[DNS] TXT record",
                    f"Published in a TXT record for {registered_domain}: {txt[:120]}"
                )
    except Exception as exc:
        logger.info(f"No usable TXT records for '{registered_domain}': {exc}")

    return occurrences


async def lookup_dns_contact_emails(registered_domain: str) -> List[EmailOccurrence]:
    """Async wrapper around the blocking dnspython lookups."""
    occurrences = await asyncio.to_thread(_lookup_dns_contacts_blocking, registered_domain)

    if occurrences:
        logger.info(f"DNS records yielded {len(occurrences)} address(es).")

    return occurrences


def _walk_rdap_entities(entities: List[Dict], collected: List[Tuple[str, str]]) -> None:
    """Depth-first walk of RDAP entities, collecting (role, email) pairs."""
    for entity in entities or []:
        roles = ", ".join(entity.get("roles", [])) or "contact"

        vcard = entity.get("vcardArray")
        if isinstance(vcard, list) and len(vcard) > 1:
            for field in vcard[1]:
                # Each field is [name, params, type, value]
                if isinstance(field, list) and len(field) >= 4 and field[0] == "email":
                    value = field[3]
                    if isinstance(value, str):
                        collected.append((roles, value))

        _walk_rdap_entities(entity.get("entities", []), collected)


async def fetch_rdap_contact_emails(
    registered_domain: str,
    client: httpx.AsyncClient,
    timeout: float = 20.0
) -> Tuple[List[EmailOccurrence], Optional[ScanError]]:
    """
    Reads registrar and registrant contacts from RDAP, the structured WHOIS successor.

    Most gTLD registries redact these behind GDPR, in which case only an abuse
    address comes back — still a working mailbox worth capturing.
    """
    url = f"{RDAP_BOOTSTRAP_URL}{registered_domain}"

    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
    except Exception as exc:
        return [], ScanError(url=url, error_type="RDAP Error", message=str(exc))

    if resp.status_code != 200:
        return [], ScanError(
            url=url,
            status_code=resp.status_code,
            error_type="RDAP Error",
            message=f"RDAP lookup returned HTTP {resp.status_code}"
        )

    try:
        payload = resp.json()
    except Exception as exc:
        return [], ScanError(url=url, error_type="RDAP Error", message=f"Unparseable response: {exc}")

    collected: List[Tuple[str, str]] = []
    _walk_rdap_entities(payload.get("entities", []), collected)

    occurrences: List[EmailOccurrence] = []
    seen: Set[str] = set()

    for role, raw_email in collected:
        occ = _build_occurrence(
            raw_email=raw_email,
            source_url=url,
            source_title=f"[RDAP] {role}",
            source_type="rdap",
            context=f"Listed as the '{role}' contact in the RDAP record for {registered_domain}"
        )
        if occ and occ.email not in seen:
            seen.add(occ.email)
            occurrences.append(occ)

    if occurrences:
        logger.info(f"RDAP yielded {len(occurrences)} address(es) for '{registered_domain}'.")

    return occurrences, None


async def collect_wellknown_emails(
    registered_domain: str,
    origins: List[str],
    client: httpx.AsyncClient,
    timeout: float = 10.0
) -> Tuple[List[EmailOccurrence], List[ScanError]]:
    """
    Runs every standards-based lookup concurrently.

    Returns:
        Tuple[List[EmailOccurrence], List[ScanError]]: (occurrences, errors)
    """
    security_txt_task = fetch_security_txt_emails(origins, client, timeout)
    dns_task = lookup_dns_contact_emails(registered_domain)
    rdap_task = fetch_rdap_contact_emails(registered_domain, client, timeout * 2)

    security_occs, dns_occs, (rdap_occs, rdap_error) = await asyncio.gather(
        security_txt_task, dns_task, rdap_task
    )

    occurrences = [*security_occs, *dns_occs, *rdap_occs]
    errors = [rdap_error] if rdap_error else []

    logger.info(
        f"Well-known sources for '{registered_domain}': "
        f"{len(security_occs)} from security.txt, {len(dns_occs)} from DNS, "
        f"{len(rdap_occs)} from RDAP."
    )

    return occurrences, errors
