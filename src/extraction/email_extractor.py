"""
Deterministic email extraction module covering regex, mailto links, and obfuscated pattern detection.
"""

import re
from typing import List, Set

from src.models.email import EmailOccurrence
from src.validation.email_validator import (
    is_false_positive_email,
    is_valid_email_syntax,
    normalize_email_address,
)

# Regex for standard emails
STANDARD_EMAIL_REGEX = re.compile(
    r"[^\s<>(),;:\"\']+@[^\s<>(),;:\"\']+\.[^\s<>(),;:\"\'.]{2,}",
    re.IGNORECASE
)

# Regex patterns for publicly displayed obfuscation.
#
# A bare " at " is also an ordinary English word, so it may only pair with an
# equally explicit dot marker. Allowing " at " together with a literal "." turns
# prose such as "content available at www.example.com. Notice ..." into a bogus
# address, which is why the two cases are kept as separate patterns.
_BRACKETED_AT = r"\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}"
_BRACKETED_DOT = r"\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}"
_WORDED_AT = r"\s+(?:at|arroba|bei|على)\s+"
_WORDED_DOT = r"\s+(?:dot|punkt|punto|点|डॉट|نقطة)\s+"

OBFUSCATED_PATTERNS = [
    # john [at] example [dot] com  /  john (at) example.com
    re.compile(
        rf"([a-zA-Z0-9._%+-]+)\s*(?:{_BRACKETED_AT})\s*"
        rf"([a-zA-Z0-9.-]+)\s*(?:{_BRACKETED_DOT}|\.)\s*([a-zA-Z]{{2,}})",
        re.IGNORECASE
    ),
    # john AT example DOT com — both separators must be spelled out
    re.compile(
        rf"([a-zA-Z0-9._%+-]+)(?:{_WORDED_AT})"
        rf"([a-zA-Z0-9.-]+)\s*(?:{_BRACKETED_DOT}|{_WORDED_DOT})\s*([a-zA-Z]{{2,}})",
        re.IGNORECASE
    ),
]


def extract_emails_from_text(
    text: str,
    source_url: str,
    source_title: str = "",
    source_type: str = "visible_text"
) -> List[EmailOccurrence]:
    """
    Extracts standard and obfuscated email addresses from plain text.
    """
    if not text or not isinstance(text, str):
        return []

    occurrences: List[EmailOccurrence] = []
    seen_emails_for_page: Set[str] = set()

    # 1. Standard regex matching
    for match in STANDARD_EMAIL_REGEX.finditer(text):
        raw_email = match.group(0)
        norm_email = normalize_email_address(raw_email)

        if norm_email and norm_email not in seen_emails_for_page:
            if is_valid_email_syntax(norm_email) and not is_false_positive_email(norm_email):
                seen_emails_for_page.add(norm_email)
                occurrences.append(EmailOccurrence(
                    email=norm_email,
                    source_url=source_url,
                    source_title=source_title,
                    source_type=source_type,
                    context="",  # Context populated separately
                    is_mailto=False,
                    is_obfuscated=False,
                ))

    # 2. Obfuscated email reconstruction
    for pattern in OBFUSCATED_PATTERNS:
        for match in pattern.finditer(text):
            user_part, domain_part, tld_part = match.groups()
            # Skip if match looks like a normal sentence ("look at example dot com" with huge space or generic words)
            if len(user_part.strip()) > 64 or len(domain_part.strip()) > 100:
                continue

            reconstructed = f"{user_part.strip()}@{domain_part.strip()}.{tld_part.strip()}"
            norm_email = normalize_email_address(reconstructed)

            if norm_email and norm_email not in seen_emails_for_page:
                if is_valid_email_syntax(norm_email) and not is_false_positive_email(norm_email):
                    seen_emails_for_page.add(norm_email)
                    occurrences.append(EmailOccurrence(
                        email=norm_email,
                        source_url=source_url,
                        source_title=source_title,
                        source_type="obfuscated",
                        context="",
                        is_mailto=False,
                        is_obfuscated=True,
                    ))

    return occurrences


def process_mailto_emails(
    mailto_emails: List[str],
    source_url: str,
    source_title: str = ""
) -> List[EmailOccurrence]:
    """
    Processes explicit mailto href emails into EmailOccurrence records with high priority.
    """
    occurrences: List[EmailOccurrence] = []
    seen: Set[str] = set()

    for raw in mailto_emails:
        norm = normalize_email_address(raw)
        if norm and norm not in seen:
            if is_valid_email_syntax(norm) and not is_false_positive_email(norm):
                seen.add(norm)
                occurrences.append(EmailOccurrence(
                    email=norm,
                    source_url=source_url,
                    source_title=source_title,
                    source_type="mailto",
                    context="",
                    is_mailto=True,
                    is_obfuscated=False,
                ))

    return occurrences
