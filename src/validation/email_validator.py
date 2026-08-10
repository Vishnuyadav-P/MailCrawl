"""
Email syntax validation and false positive filtering module.
"""

import re

import tldextract

EMAIL_SYNTAX_REGEX = re.compile(
    r"^[^\s@]+@[^\s@]+\.[^\s@.]{2,}$"
)

# Percent-encoded sequences that survive scraping of 'mailto:' hrefs and page text,
# e.g. 'mailto:%20abhishek.jain2@wipro.com' or 'info%40example.com'. Only whitespace,
# quoting and the two structural characters are decoded, so a literal '%' inside a
# local part is left untouched.
PERCENT_ESCAPE_MAP = {
    "20": " ", "09": " ", "0a": " ", "0b": " ", "0c": " ", "0d": " ",
    "22": '"', "27": "'", "3c": "<", "3e": ">",
    "2e": ".", "40": "@",
}
PERCENT_ESCAPE_REGEX = re.compile(
    "%(" + "|".join(PERCENT_ESCAPE_MAP) + ")",
    re.IGNORECASE
)

# A local part may legitimately open with an underscore, but never with punctuation
# such as '.', '-' or a leftover '%'; a top-level domain always ends on a letter.
LEADING_JUNK_REGEX = re.compile(r"^[^\w]+", re.UNICODE)
TRAILING_JUNK_REGEX = re.compile(r"[^\w]+$", re.UNICODE)

FALSE_POSITIVE_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "css", "js",
    "pdf", "doc", "docx", "zip", "tar", "gz", "mp4", "mp3", "wav",
    "woff", "woff2", "ttf", "eot"
}

PLACEHOLDER_EMAILS = {
    "username@domain.com", "yourname@domain.com", "name@domain.com",
    "test@test.com", "email@domain.com", "user@domain.com"
}


from functools import lru_cache

@lru_cache(maxsize=8192)
def is_valid_email_syntax(email: str) -> bool:
    """Validates basic syntax according to standard email specifications."""
    if not email or not isinstance(email, str):
        return False

    email = email.strip()
    if len(email) > 254:
        return False

    if not EMAIL_SYNTAX_REGEX.match(email):
        return False

    parts = email.split("@")
    if len(parts) != 2:
        return False

    local_part, domain_part = parts
    if not local_part or len(local_part) > 64:
        return False

    if not domain_part or len(domain_part) > 255:
        return False
    if domain_part.startswith("-") or ".." in domain_part:
        return False
    try:
        domain_part.encode("idna")
    except UnicodeError:
        return False
    return True


@lru_cache(maxsize=8192)
def is_false_positive_email(email: str) -> bool:
    """Identifies common extraction false positives (e.g. image filenames, placeholder examples)."""
    if not is_valid_email_syntax(email):
        return True

    email_lower = email.lower().strip()

    if email_lower in PLACEHOLDER_EMAILS:
        return True

    domain_part = email_lower.split("@")[-1]
    tld_suffix = domain_part.split(".")[-1]

    if tld_suffix in FALSE_POSITIVE_EXTENSIONS:
        return True

    # No real mailbox lives under a 'www.' host; this only ever comes from prose
    # such as "available at www.example.com" being reconstructed as an address.
    if domain_part.startswith("www."):
        return True

    # Check if top-level domain or suffix is actually a static asset file extension
    tld_extracted = tldextract.extract(domain_part)
    suffix = tld_extracted.suffix.lower()

    if suffix in FALSE_POSITIVE_EXTENSIONS:
        return True

    # An empty suffix means the ending is not a real public suffix at all
    # ('matrix@all.therefore'), which regex alone cannot tell from a valid address.
    if not suffix:
        return True

    # Reject if local part contains common file extensions
    local_part = email_lower.split("@")[0]
    for ext in FALSE_POSITIVE_EXTENSIONS:
        if local_part.endswith(f".{ext}"):
            return True

    return False


@lru_cache(maxsize=8192)
def normalize_email_address(email: str) -> str:
    """Normalizes an email address to lowercase and strips surrounding whitespace/punctuation."""
    if not email:
        return ""

    cleaned = email.strip().casefold()

    # Remove mailto: prefix if present
    if cleaned.startswith("mailto:"):
        cleaned = cleaned[7:]

    # Remove query string or fragments if present
    cleaned = cleaned.split("?")[0].split("#")[0]

    # Decode leftover URL escapes, e.g. '%20abhishek.jain2@wipro.com'
    cleaned = PERCENT_ESCAPE_REGEX.sub(
        lambda match: PERCENT_ESCAPE_MAP[match.group(1).lower()],
        cleaned
    )

    # Decoding can expose surrounding words ('contact us at info@example.com'),
    # so keep only the whitespace-delimited token that holds the address.
    if any(char.isspace() for char in cleaned):
        tokens = [token for token in cleaned.split() if "@" in token]
        cleaned = tokens[0] if tokens else ""

    # Strip surrounding punctuation like '<john@example.com>' or 'john@example.com.'
    cleaned = cleaned.strip("<>(),;:\"'[]").strip()
    cleaned = LEADING_JUNK_REGEX.sub("", cleaned)
    cleaned = TRAILING_JUNK_REGEX.sub("", cleaned)

    return cleaned

