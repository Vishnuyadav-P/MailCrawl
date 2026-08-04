"""
Data models for scan configuration, statistics, progress, and errors.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ScanConfig(BaseModel):
    """Configuration options provided by the user for a domain crawl job."""
    target_domain: str
    max_depth: int = Field(default=0, ge=0)  # 0 = follow links as deep as the site goes
    timeout: int = Field(default=10, ge=1, le=60)
    concurrent_requests: int = Field(default=10, ge=1, le=50)
    include_pdfs: bool = True
    js_rendering: str = Field(default="automatic")  # automatic, always, never
    only_target_domain: bool = True

    # Crawl every reachable host under the registered domain, not just the apex
    include_subdomains: bool = True
    # Scan off-domain company presences (careers portals, press rooms, profiles)
    include_external_sources: bool = True
    # Read security.txt, DMARC/SOA DNS records and RDAP registration contacts
    include_wellknown_sources: bool = True
    extra_source_urls: List[str] = Field(default_factory=list)

    # robots.txt is always fetched for its Sitemap: lines; this only controls
    # whether its Disallow rules are enforced.
    respect_robots: bool = True
    # Pages between checkpoint writes; 0 disables checkpointing
    checkpoint_every_pages: int = Field(default=50, ge=0)
    enable_pdf_ocr: bool = False
    ocr_languages: str = "eng"
    # SMTP recipient checks are intentionally opt-in and not implemented by default.
    enable_smtp_verification: bool = False


class ScanRecord(BaseModel):
    """One entry in the persistent scan log, written when a scan starts and updated when it ends."""
    scan_id: str
    search_name: str
    target_domain: str
    started_at: str
    finished_at: str = ""
    duration_seconds: float = 0.0
    # running, completed, interrupted, failed
    status: str = "running"
    pages_scanned: int = 0
    sites_discovered: int = 0
    unique_emails: int = 0
    resumed_from: Optional[str] = None
    message: str = ""


class ScanError(BaseModel):
    """Error record logged during crawling or processing."""
    url: str
    status_code: Optional[int] = None
    error_type: str = "Unknown"
    message: str = ""


class ScanStats(BaseModel):
    """Overall statistics collected during scan execution."""
    domain: str = ""
    start_time: str = ""
    finish_time: str = ""
    pages_discovered: int = 0
    pages_scanned: int = 0
    html_pages: int = 0
    pdf_files: int = 0
    sites_discovered: int = 0  # Reachable hosts under the registered domain
    external_sources_scanned: int = 0
    wellknown_emails_found: int = 0  # From security.txt, DNS and RDAP
    robots_rules_ignored: int = 0  # URLs crawled despite a robots.txt Disallow
    raw_email_occurrences: int = 0
    unique_emails: int = 0
    target_domain_emails: int = 0
    scan_duration_seconds: float = 0.0
    error_count: int = 0


class ScanProgress(BaseModel):
    """Real-time progress notification pushed to the live scan feed."""
    status_message: str = ""
    pages_discovered: int = 0  # Total pages known for the domain; grows as links surface
    pages_scanned: int = 0
    current_url: str = ""
    raw_emails_found: int = 0
    unique_emails_found: int = 0
    pdf_processed_count: int = 0
    pdf_total_count: int = 0
