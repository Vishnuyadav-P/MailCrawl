"""
Processing package initialization.
"""

from src.processing.deduplicator import deduplicate_and_process_emails
from src.processing.scoring import calculate_confidence_score

__all__ = [
    "deduplicate_and_process_emails",
    "calculate_confidence_score",
]
