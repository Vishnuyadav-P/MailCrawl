"""
The result filter.

This is the single predicate behind both GET /results and the CSV/XLSX exports, so
what is on screen and what is downloaded cannot drift apart.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.models.email import CanonicalEmailResult


@dataclass(frozen=True)
class FilterSpec:
    """The filter inputs a client can supply."""
    q: Optional[str] = None
    email_type: Optional[str] = None
    validation_status: Optional[str] = None
    domain_match: Optional[str] = None
    min_confidence: int = 0

    @classmethod
    def from_query(
        cls,
        q: Optional[str] = None,
        email_type: Optional[str] = None,
        validation_status: Optional[str] = None,
        domain_match: Optional[str] = None,
        min_confidence: int = 0,
    ) -> "FilterSpec":
        # "All" is the UI's no-op sentinel for a dropdown left at its default.
        def clean(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            value = value.strip()
            return None if value in ("", "All") else value

        return cls(
            q=clean(q),
            email_type=clean(email_type),
            validation_status=clean(validation_status),
            domain_match=clean(domain_match),
            min_confidence=max(0, min(100, min_confidence or 0)),
        )

    @property
    def is_empty(self) -> bool:
        return not any([
            self.q, self.email_type, self.validation_status,
            self.domain_match, self.min_confidence,
        ])


def matches(result: CanonicalEmailResult, spec: FilterSpec) -> bool:
    """Whether one result survives the filter. Criteria are ANDed."""
    if spec.q:
        needle = spec.q.lower()
        haystacks = (
            result.email,
            result.name,
            result.role,
            result.department,
        )
        # A null field never matches.
        if not any(field and needle in field.lower() for field in haystacks):
            return False

    if spec.email_type and result.email_type != spec.email_type:
        return False

    if spec.validation_status and result.validation_status != spec.validation_status:
        return False

    if spec.domain_match and result.domain_match != spec.domain_match:
        return False

    # Only applied above zero, so a result with no confidence is not excluded by default.
    if spec.min_confidence > 0 and result.confidence < spec.min_confidence:
        return False

    return True


def apply_filters(
    results: List[CanonicalEmailResult],
    spec: FilterSpec,
) -> List[CanonicalEmailResult]:
    """Filters a result set, preserving order."""
    if spec.is_empty:
        return list(results)
    return [r for r in results if matches(r, spec)]


def build_facets(results: List[CanonicalEmailResult]) -> Dict[str, List[str]]:
    """
    Option lists for the filter dropdowns.

    Built from the UNFILTERED set so the dropdowns do not collapse as a filter is
    applied.
    """
    return {
        "email_type": sorted({r.email_type for r in results if r.email_type}),
        "validation_status": sorted({r.validation_status for r in results if r.validation_status}),
        "domain_match": sorted({r.domain_match for r in results if r.domain_match}),
        "source_type": sorted({r.source_type for r in results if r.source_type}),
    }
