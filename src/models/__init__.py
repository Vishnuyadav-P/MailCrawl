"""
Models package initialization.
"""

from src.models.email import CanonicalEmailResult, EmailOccurrence
from src.models.scan import ScanConfig, ScanError, ScanProgress, ScanStats

__all__ = [
    "EmailOccurrence",
    "CanonicalEmailResult",
    "ScanConfig",
    "ScanStats",
    "ScanProgress",
    "ScanError",
]
