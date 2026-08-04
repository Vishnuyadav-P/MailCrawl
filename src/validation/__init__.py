"""
Validation package initialization.
"""

from src.validation.domain_validator import (
    check_domain_mx_records,
    check_host_ssrf,
    is_ip_prohibited,
    validate_url_ssrf,
)
from src.validation.email_validator import (
    is_false_positive_email,
    is_valid_email_syntax,
    normalize_email_address,
)

__all__ = [
    "validate_url_ssrf",
    "check_host_ssrf",
    "check_domain_mx_records",
    "is_ip_prohibited",
    "is_valid_email_syntax",
    "is_false_positive_email",
    "normalize_email_address",
]
