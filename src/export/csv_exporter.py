"""
CSV export utility.

Built on the standard library rather than pandas. The only operation needed is
writing a flat list of records in a fixed column order; pandas would add a
heavyweight dependency, and its import cost, for a DataFrame that is constructed
and immediately serialized.
"""

import csv
import io
from typing import Any, List

from src.models.email import CanonicalEmailResult

# Column order is part of the export contract — the XLSX 'Emails' sheet mirrors it.
CSV_COLUMNS = [
    "Email", "Name",
    "Source URL", "Source Title", "Source Type", "Occurrences",
    "Validation Status", "Domain Match", "Confidence",
]


def _row(item: CanonicalEmailResult) -> List[Any]:
    """Flattens one result into CSV_COLUMNS order."""
    return [
        item.email,
        item.name or "",
        item.source_url,
        item.source_title,
        item.source_type,
        item.occurrences,
        item.validation_status,
        item.domain_match,
        item.confidence,
    ]


def generate_csv_bytes(results: List[CanonicalEmailResult]) -> bytes:
    """Generates UTF-8 CSV bytes from canonical email results."""
    # newline="" is what csv expects: the writer emits its own terminators, and
    # without it StringIO would translate them a second time.
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(CSV_COLUMNS)
    writer.writerows(_row(item) for item in results)

    return buffer.getvalue().encode("utf-8")
