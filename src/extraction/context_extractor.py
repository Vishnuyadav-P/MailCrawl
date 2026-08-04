"""
Context extraction module isolating the text surrounding a discovered address.
"""

import re


def extract_surrounding_context(
    text: str,
    target_email: str,
    max_context_chars: int = 600
) -> str:
    """
    Extracts approximately max_context_chars (300-1000) of text surrounding target_email occurrence.

    Args:
        text (str): Full visible page text.
        target_email (str): Discovered email address.
        max_context_chars (int): Maximum total characters of surrounding context.

    Returns:
        str: Snippet centered around target_email.
    """
    if not text or not target_email:
        return ""

    # Find position of target_email in text (case insensitive search)
    idx = text.lower().find(target_email.lower())
    if idx == -1:
        # If exact string not found (e.g. obfuscated), return first 600 chars of page
        return text[:max_context_chars].strip()

    half_window = max_context_chars // 2
    start_pos = max(0, idx - half_window)
    end_pos = min(len(text), idx + len(target_email) + half_window)

    snippet = text[start_pos:end_pos]

    # Clean up whitespace
    cleaned_snippet = re.sub(r"\s+", " ", snippet).strip()

    prefix = "..." if start_pos > 0 else ""
    suffix = "..." if end_pos < len(text) else ""

    return f"{prefix}{cleaned_snippet}{suffix}"
