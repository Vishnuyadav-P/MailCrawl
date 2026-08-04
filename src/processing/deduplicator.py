"""
Email deduplication, source url prioritization, and result merging module.
"""

from collections import defaultdict
from typing import Dict, List

from src.models.email import CanonicalEmailResult, EmailOccurrence
from src.processing.scoring import calculate_confidence_score
from src.utils.urls import get_url_priority, is_same_registered_domain
from src.validation.domain_validator import get_email_verification, validate_email_for_domain


def _get_source_rank(occ: EmailOccurrence) -> int:
    """Returns rank priority for selecting representative best source URL (higher is better)."""
    url_priority = get_url_priority(occ.source_url)
    type_bonus = 0
    if occ.is_mailto:
        type_bonus += 20
    if occ.source_type == "pdf":
        type_bonus -= 10  # Prefer HTML source over PDF if available

    return url_priority + type_bonus


def deduplicate_and_process_emails(
    occurrences: List[EmailOccurrence],
    registered_domain: str,
    only_target_domain: bool = True,
) -> List[CanonicalEmailResult]:
    """
    Aggregates raw email occurrences into deduplicated canonical results.
    """
    if not occurrences:
        return []

    # Group occurrences by email
    grouped: Dict[str, List[EmailOccurrence]] = defaultdict(list)
    for occ in occurrences:
        email = occ.email.lower()
        if only_target_domain and not is_same_registered_domain(email, registered_domain):
            continue

        grouped[email].append(occ)

    results: List[CanonicalEmailResult] = []

    for email, occ_list in grouped.items():
        # Only the top-ranked occurrence is used, so a full sort would be wasted work.
        best_occ = max(occ_list, key=_get_source_rank)

        # Extract unique source URLs
        unique_sources = list({occ.source_url for occ in occ_list if occ.source_url})

        # Email domain
        email_domain = email.split("@")[-1] if "@" in email else registered_domain

        # Syntax + deliverability + target-domain match
        validation_status, domain_match = validate_email_for_domain(email, registered_domain)

        # Confidence score
        conf_score = calculate_confidence_score(
            email=email,
            occurrences=occ_list,
            registered_domain=registered_domain,
        )

        verification = get_email_verification(email)
        canonical = CanonicalEmailResult(
            email=email,
            name=None,
            role=None,
            department=None,
            email_type="unknown",
            purpose=None,
            domain=email_domain,
            source_url=best_occ.source_url,
            source_title=best_occ.source_title,
            source_type=best_occ.source_type,
            occurrences=len(occ_list),
            all_sources=unique_sources,
            validation_status=validation_status,
            domain_match=domain_match,
            confidence=max(0, min(100, conf_score + verification.confidence_adjustment)),
            language=best_occ.language,
            verification=verification,
        )
        results.append(canonical)

    # Sort results by confidence descending
    results.sort(key=lambda r: r.confidence, reverse=True)

    return results
