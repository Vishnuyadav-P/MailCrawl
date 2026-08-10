"""
Domain validation, MX record lookup, and SSRF security protection.
"""

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse

import dns.resolver
import tldextract

from src.utils.logging import logger
from src.validation.email_validator import is_valid_email_syntax, is_false_positive_email
from src.models.email import EmailVerification
from datetime import datetime, timezone

PROHIBITED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.88.99.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_ip_prohibited(ip_str: str) -> bool:
    """Checks whether an IP string falls into private, loopback, or cloud metadata ranges."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
        for net in PROHIBITED_IP_NETWORKS:
            if ip_obj in net:
                return True
        return False
    except ValueError:
        return True  # If invalid IP, treat as unsafe


@lru_cache(maxsize=4096)
def check_host_ssrf(hostname: str) -> tuple[bool, str]:
    """
    Resolves a hostname and rejects it if any address falls in a prohibited range.

    Cached per host because the crawler runs this once per URL, while a whole crawl
    typically covers a handful of hosts — an uncached scan pays one blocking
    getaddrinfo per page. The cache is cleared between scans by the server's cleanup
    loop, so a host is never pinned to a stale verdict for the life of the process.

    Caching does not weaken the check. The verdict is only advisory to begin with:
    httpx resolves the name again when it connects, so a rebinding attacker was
    always able to return a different address to the second lookup. The defence that
    actually holds is the connection itself refusing to reach a private range.
    """
    if hostname in ("localhost", "0.0.0.0", "metadata.google.internal"):
        return False, f"Prohibited host name '{hostname}'"

    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False, f"Failed to resolve DNS for host '{hostname}'"

    for item in addr_info:
        ip_str = item[4][0]
        if is_ip_prohibited(ip_str):
            return False, f"Host '{hostname}' resolved to prohibited IP '{ip_str}'"

    return True, ""


def validate_url_ssrf(url: str) -> tuple[bool, str]:
    """
    Validates a URL against SSRF vulnerabilities by checking its scheme and resolving host IPs.

    Returns:
        tuple[bool, str]: (is_safe, error_reason)
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Unsupported scheme '{parsed.scheme}'"

        hostname = parsed.hostname
        if not hostname:
            return False, "Missing hostname in URL"

        return check_host_ssrf(hostname.lower())
    except Exception as exc:
        return False, f"SSRF check failed: {exc}"


@lru_cache(maxsize=2048)
def check_domain_mx_records(domain: str) -> str:
    """
    Checks if a domain has valid MX (Mail Exchange) DNS records.

    Returns:
        str: "Valid Domain", "No MX Record", "Invalid Syntax", or "Unknown"
    """
    if not domain or not isinstance(domain, str):
        return "Invalid Syntax"

    domain_clean = domain.strip().lower()
    try:
        domain_clean = domain_clean.encode("idna").decode("ascii")
    except UnicodeError:
        return "Invalid Syntax"
    if not domain_clean or "." not in domain_clean:
        return "Invalid Syntax"

    try:
        answers = dns.resolver.resolve(domain_clean, "MX", lifetime=3.0)
        if answers and len(answers) > 0:
            return "Valid Domain"
        return "No MX Record"
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        # Fallback check for A record (RFC 5321 allows implicit MX via A/AAAA)
        try:
            a_answers = dns.resolver.resolve(domain_clean, "A", lifetime=3.0)
            if a_answers and len(a_answers) > 0:
                return "Valid Domain"
        except Exception:
            pass
        return "No MX Record"
    except (dns.resolver.Timeout, dns.exception.DNSException) as exc:
        logger.warning(f"DNS resolution timeout for domain '{domain_clean}': {exc}")
        return "Unknown"
    except Exception as exc:
        logger.error(f"Unexpected error checking MX for '{domain_clean}': {exc}")
        return "Unknown"


@lru_cache(maxsize=4096)
def classify_domain_match(email: str, target_registered_domain: str) -> str:
    """
    Describes how an email address's host relates to the domain being scanned.

    Returns:
        str: "Target Domain", "Subdomain", "External Domain", or "Unknown"
    """
    if not email or "@" not in email or not target_registered_domain:
        return "Unknown"

    email_host = email.rsplit("@", 1)[-1].strip().lower()
    target = target_registered_domain.strip().lower()

    extracted = tldextract.extract(email_host)
    if not extracted.domain or not extracted.suffix:
        return "Unknown"

    email_registered = f"{extracted.domain}.{extracted.suffix}"
    if email_registered != target:
        return "External Domain"

    # Same registered domain: separate the apex from hosts like 'mail.example.com'
    return "Target Domain" if email_host in (target, f"www.{target}") else "Subdomain"


def validate_email_for_domain(email: str, target_registered_domain: str) -> tuple[str, str]:
    """
    Validates an address on syntax, deliverability, and whether it belongs to the
    domain being scanned.

    Returns:
        tuple[str, str]: (validation_status, domain_match)
    """
    if not is_valid_email_syntax(email):
        return "Invalid Syntax", "Unknown"

    domain_match = classify_domain_match(email, target_registered_domain)
    email_host = email.rsplit("@", 1)[-1].strip().lower()
    mx_status = check_domain_mx_records(email_host)

    if mx_status != "Valid Domain":
        return mx_status, domain_match

    if domain_match == "External Domain":
        return "Valid Domain – External", domain_match

    if domain_match == "Unknown":
        return "Valid Domain", domain_match

    return "Valid Domain – Target", domain_match


ROLE_PREFIXES = {"admin", "billing", "careers", "contact", "help", "hr", "info", "legal", "privacy", "sales", "security", "support"}
DISPOSABLE_DOMAINS = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com"}

import smtplib

@lru_cache(maxsize=1024)
def check_smtp_mailbox(email: str, host: str) -> tuple[str, str]:
    """
    Connects to the MX record for the host and checks if the recipient exists.
    Returns ('valid'|'invalid'|'unknown', response_string).
    """
    try:
        answers = dns.resolver.resolve(host, "MX", lifetime=3.0)
        mx_record = sorted(answers, key=lambda r: r.preference)[0].exchange.to_text()
    except Exception as e:
        return "unknown", f"DNS MX error: {e}"

    try:
        with smtplib.SMTP(mx_record, timeout=5.0) as server:
            server.helo("mailcrawl.local")
            code_mail, msg_mail = server.mail("test@example.com")
            if code_mail != 250:
                return "unknown", f"Sender rejected: {code_mail} {msg_mail.decode(errors='ignore')}"
                
            code_rcpt, msg_rcpt = server.rcpt(email)
            resp_str = f"{code_rcpt} {msg_rcpt.decode(errors='ignore')}"
            if code_rcpt == 250:
                return "valid", resp_str
            elif code_rcpt >= 500:
                return "invalid", resp_str
            return "unknown", resp_str
    except Exception as exc:
        logger.warning(f"SMTP check failed for {email} via {mx_record}: {exc}")
        return "unknown", f"SMTP error: {exc}"

def get_email_verification(email: str, probe_smtp: bool = False) -> EmailVerification:
    """Verifies syntax, MX records, and optionally actively probes SMTP for the mailbox."""
    syntax = is_valid_email_syntax(email)
    is_fp = is_false_positive_email(email)
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not syntax or is_fp:
        return EmailVerification(syntax_valid=False, checked_at=checked, provider_notes="Invalid address syntax or false positive")
    local, host = email.rsplit("@", 1)
    mx = check_domain_mx_records(host)
    disposable = host.encode("idna").decode("ascii") in DISPOSABLE_DOMAINS
    role = local.casefold() in ROLE_PREFIXES
    
    mx_status = "valid" if mx == "Valid Domain" else "absent" if mx == "No MX Record" else "unknown"
    
    mailbox_status = "unverified"
    provider_notes = "DNS-only verification; no SMTP recipient probe performed."
    if probe_smtp and mx_status == "valid":
        mailbox_status, provider_notes = check_smtp_mailbox(email, host)
    
    adjustment = (-20 if disposable else 0) + (-5 if role else 0) + (20 if mailbox_status == "valid" else -50 if mailbox_status == "invalid" else 0)
    
    return EmailVerification(
        syntax_valid=True, domain_resolves=(mx != "No MX Record"),
        mx_status=mx_status,
        mailbox_status=mailbox_status, disposable=disposable, role_account=role,
        checked_at=checked, provider_notes=provider_notes,
        confidence_adjustment=adjustment,
    )
