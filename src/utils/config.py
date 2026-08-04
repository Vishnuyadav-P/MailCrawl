"""
Environment and application configuration manager.
"""

import os

from dotenv import load_dotenv

# Load environment variables from .env file if available
load_dotenv()


class Config:
    """Application configuration settings loaded from environment variables."""

    # Where the scan log, saved results and crawl checkpoints are kept
    DATA_DIR: str = os.getenv("DATA_DIR", "data")

    # Request options
    DEFAULT_USER_AGENT: str = (
        "DomainEmailIntelligenceBot/1.0 (+https://github.com/domain-email-intelligence)"
    )
    MAX_RESPONSE_SIZE_BYTES: int = int(
        os.getenv("MAX_RESPONSE_SIZE_MB", "25")
    ) * 1024 * 1024
    MAX_PDF_SIZE_BYTES: int = int(
        os.getenv("MAX_PDF_SIZE_MB", "150")
    ) * 1024 * 1024

    # Subdomain discovery: every host under the registered domain is a crawl target.
    # Coverage is the goal here, so nothing is capped by default — set the limits
    # below only when a scan needs to be deliberately cut short.
    SUBDOMAIN_DISCOVERY_ENABLED: bool = os.getenv("SUBDOMAIN_DISCOVERY", "true").lower() == "true"
    CRT_SH_TIMEOUT_SECONDS: float = float(os.getenv("CRT_SH_TIMEOUT_SECONDS", "120"))
    MAX_SUBDOMAINS: int = int(os.getenv("MAX_SUBDOMAINS", "0"))  # 0 = unlimited
    MAX_EXTERNAL_SOURCES: int = int(os.getenv("MAX_EXTERNAL_SOURCES", "0"))  # 0 = unlimited

    # Hosts commonly used for contactable content when a certificate log is unavailable
    COMMON_SUBDOMAIN_PREFIXES: tuple = (
        "www", "careers", "jobs", "contact", "about", "team", "people",
        "corporate", "investors", "ir", "press", "media", "news", "blog",
        "support", "help", "sales", "partners", "hr", "recruit", "info",
    )

    # Every host a certificate names is probed by default: dev and mail hosts rarely
    # publish contact pages, but "rarely" is not "never" and a missed address costs
    # more than a wasted request. Set SKIP_NON_PUBLIC_SUBDOMAINS=true to trade that
    # coverage for a faster scan.
    SKIP_NON_PUBLIC_SUBDOMAINS: bool = os.getenv(
        "SKIP_NON_PUBLIC_SUBDOMAINS", "false"
    ).lower() == "true"
    NON_PUBLIC_SUBDOMAIN_MARKERS: tuple = (
        "dev", "qa", "uat", "test", "stage", "staging", "sandbox", "demo",
        "internal", "intranet", "vpn", "citrix", "sso", "auth", "adfs", "okta",
        "smtp", "imap", "pop", "mx", "autodiscover", "lyncdiscover", "owa",
        "exchange", "mail", "webmail", "ftp", "sftp", "git", "jenkins", "jira",
        "nonprod", "preprod", "np", "pr",
    )
