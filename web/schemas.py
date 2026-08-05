"""
Request bodies for the API.

Responses are plain dicts built by web/services/serializers.py — they mirror the
pydantic models in src/models, and re-declaring them here would only create a second
definition to keep in sync.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class ScanConfigInput(BaseModel):
    """
    Scan settings from the UI. Every field is optional and falls back to the
    ScanConfig default, so a client can send only what it wants to change.
    """
    max_depth: int = Field(default=0, ge=0)          # 0 = follow links as deep as the site goes
    max_pages: int = Field(default=0, ge=0)          # 0 = no limit; caps pages scanned per job
    timeout: int = Field(default=10, ge=1, le=60)
    concurrent_requests: int = Field(default=10, ge=1, le=50)
    include_pdfs: bool = True
    js_rendering: str = Field(default="automatic", pattern="^(automatic|always|never)$")
    only_target_domain: bool = True
    include_subdomains: bool = True
    include_external_sources: bool = True
    include_wellknown_sources: bool = True
    extra_source_urls: List[str] = Field(default_factory=list)
    respect_robots: bool = True
    checkpoint_every_pages: int = Field(default=50, ge=0)
    enable_pdf_ocr: bool = False
    ocr_languages: str = Field(default="eng", max_length=100)
    enable_smtp_verification: bool = False


class StartScanRequest(BaseModel):
    domain: str = Field(..., min_length=1, description="Raw user input, e.g. 'example.com'")
    search_name: Optional[str] = Field(default=None, description="Defaults to the registered domain")
    reveal_every: int = Field(default=10, ge=1, le=100, description="Live feed batch size")
    config: ScanConfigInput = Field(default_factory=ScanConfigInput)


class ResumeScanRequest(BaseModel):
    reveal_every: int = Field(default=10, ge=1, le=100)


class ScheduleInput(BaseModel):
    target_domain: str = Field(..., min_length=1)
    search_name: Optional[str] = None
    scan_config: ScanConfigInput = Field(default_factory=ScanConfigInput)
    cron: str = Field(default="0 9 * * 1", min_length=9, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True
    notification_url: Optional[str] = None


class ScheduleUpdate(BaseModel):
    search_name: Optional[str] = None
    scan_config: Optional[ScanConfigInput] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    notification_url: Optional[str] = None
