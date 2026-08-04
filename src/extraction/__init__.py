"""
Extraction package initialization.
"""

__all__ = [
    "extract_from_html",
    "HTMLExtractionResult",
    "extract_emails_from_text",
    "process_mailto_emails",
    "extract_surrounding_context",
    "extract_emails_from_pdf_bytes",
]


def __getattr__(name):
    if name == "extract_surrounding_context":
        from src.extraction.context_extractor import extract_surrounding_context

        return extract_surrounding_context
    if name in {"extract_emails_from_text", "process_mailto_emails"}:
        from src.extraction.email_extractor import extract_emails_from_text, process_mailto_emails

        return {
            "extract_emails_from_text": extract_emails_from_text,
            "process_mailto_emails": process_mailto_emails,
        }[name]
    if name in {"HTMLExtractionResult", "extract_from_html"}:
        from src.extraction.html_extractor import HTMLExtractionResult, extract_from_html

        return {
            "HTMLExtractionResult": HTMLExtractionResult,
            "extract_from_html": extract_from_html,
        }[name]
    if name == "extract_emails_from_pdf_bytes":
        from src.extraction.pdf_extractor import extract_emails_from_pdf_bytes

        return extract_emails_from_pdf_bytes

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
