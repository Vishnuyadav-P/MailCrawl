"""
Data models for discovered email records and canonical results.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

class EmailVerification(BaseModel):
    syntax_valid: bool = False
    domain_resolves: Optional[bool] = None
    mx_status: str = "unknown"  # valid, absent, unknown
    mailbox_status: str = "unverified"
    disposable: Optional[bool] = None
    role_account: bool = False
    checked_at: str = ""
    provider_notes: str = ""
    confidence_adjustment: int = 0


class EmailOccurrence(BaseModel):
    """Raw instance of a discovered email on a specific page/document."""
    email: str
    source_url: str
    source_title: str = ""
    source_type: str = "visible_text"  # mailto, visible_text, obfuscated, pdf
    context: str = ""
    is_mailto: bool = False
    is_obfuscated: bool = False
    language: str = "unknown"
    page_number: Optional[int] = None
    ocr_confidence: Optional[float] = None


class CanonicalEmailResult(BaseModel):
    """Final merged and deduplicated email record for reporting/exporting."""
    email: str
    name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    email_type: str = "unknown"
    purpose: Optional[str] = None
    domain: str = ""
    source_url: str = ""
    source_title: str = ""
    source_type: str = "visible_text"
    occurrences: int = 1
    all_sources: List[str] = Field(default_factory=list)
    # Valid Domain – Target, Valid Domain – External, No MX Record, Invalid Syntax, Unknown
    validation_status: str = "Unknown"
    domain_match: str = "Unknown"  # Target Domain, Subdomain, External Domain, Unknown
    confidence: int = 50  # 0 to 100
    language: str = "unknown"
    verification: EmailVerification = Field(default_factory=EmailVerification)
