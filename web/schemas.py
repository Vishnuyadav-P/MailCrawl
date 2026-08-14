"""
Request bodies for the API.

Responses are plain dicts built by web/services/serializers.py — they mirror the
pydantic models in src/models, and re-declaring them here would only create a second
definition to keep in sync.
"""

from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, Field, field_validator


def _validate_cron(value: str) -> str:
    """
    Rejects a malformed cron here rather than letting croniter raise downstream.

    The store computes the next run time while building the row, so an invalid
    expression used to surface as a 500 from an ordinary bad request.
    """
    if not croniter.is_valid(value):
        raise ValueError(f"'{value}' is not a valid cron expression")
    return value


def _validate_timezone(value: str) -> str:
    """Same reasoning as the cron check: an unknown zone is a 422, not a 500."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"'{value}' is not a known IANA time zone") from exc
    return value


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
    # No min_length: croniter decides what is valid, and a character count would
    # only reject shorthands like "@daily" that it accepts.
    cron: str = Field(default="0 9 * * 1", min_length=1, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True
    notification_url: Optional[str] = None

    _check_cron = field_validator("cron")(_validate_cron)
    _check_timezone = field_validator("timezone")(_validate_timezone)


class ScheduleUpdate(BaseModel):
    search_name: Optional[str] = None
    scan_config: Optional[ScanConfigInput] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    enabled: Optional[bool] = None
    notification_url: Optional[str] = None

    @field_validator("cron")
    @classmethod
    def _check_cron(cls, value: Optional[str]) -> Optional[str]:
        return _validate_cron(value) if value is not None else value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: Optional[str]) -> Optional[str]:
        return _validate_timezone(value) if value is not None else value
