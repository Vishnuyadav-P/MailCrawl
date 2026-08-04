"""
Confidence scoring module for discovered email intelligence.
"""

from src.models.email import EmailOccurrence
from src.utils.urls import is_same_registered_domain


def calculate_confidence_score(
    email: str,
    occurrences: list[EmailOccurrence],
    registered_domain: str,
) -> int:
    """
    Computes a 0 to 100 confidence score based on deterministic evidence only.
    """
    score = 0.0

    # 1. Evidence type (up to 35 pts)
    has_mailto = any(occ.is_mailto for occ in occurrences)
    has_pdf = any(occ.source_type == "pdf" for occ in occurrences)
    has_obfuscated = any(occ.is_obfuscated for occ in occurrences)

    if has_mailto:
        score += 35.0
    elif has_obfuscated:
        score += 25.0
    elif has_pdf:
        score += 20.0
    else:
        score += 30.0  # standard visible text match

    # 2. Same target domain match (25 pts)
    if is_same_registered_domain(email, registered_domain):
        score += 25.0

    # 3. Multi-occurrence signal (up to 15 pts)
    count = len(occurrences)
    if count >= 3:
        score += 15.0
    elif count == 2:
        score += 10.0

    # Cap score between 0 and 100
    final_score = int(min(100.0, max(0.0, score)))
    return final_score
