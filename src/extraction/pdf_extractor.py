"""
PDF text extraction with a multi-engine fallback chain.

Engines are tried best-first and their output is merged, because each one breaks
lines and column layouts differently — an address mangled by one engine is often
intact in another:

  1. PyMuPDF    - fastest, best layout fidelity, native AES/RC4 decryption, and the
                  only engine here that also exposes 'mailto:' link annotations.
  2. pdfplumber - stronger on table and multi-column layouts.
  3. pypdf      - pure-python last resort. Needs 'cryptography' for AES-encrypted files.
"""

import io
import re
import unicodedata
from typing import Callable, List, Set, Tuple

from src.extraction.context_extractor import extract_surrounding_context
from src.extraction.email_extractor import extract_emails_from_text
from src.models.email import EmailOccurrence
from src.utils.logging import logger
from src.validation.email_validator import (
    is_false_positive_email,
    is_valid_email_syntax,
    normalize_email_address,
)

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover - optional engine
    fitz = None

try:
    import pdfplumber
except ImportError:  # pragma: no cover - optional engine
    pdfplumber = None

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - optional engine
    PdfReader = None


# Addresses split across a PDF line break, e.g. "john.doe@\nexample.com"
LINEBREAK_BEFORE_AT = re.compile(r"(?<=[A-Za-z0-9._%+-])[ \t]*\n[ \t]*(?=@)")
LINEBREAK_AFTER_AT = re.compile(r"(?<=@)[ \t]*\n[ \t]*(?=[A-Za-z0-9-])")
LINEBREAK_BEFORE_TLD = re.compile(r"(?<=[A-Za-z0-9])[ \t]*\n[ \t]*(?=\.[A-Za-z]{2,24}\b)")

# Words hyphenated across a line break, e.g. "Manag-\nement"
HYPHEN_LINEBREAK = re.compile(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[a-z])")

# Addresses laid out with whitespace between glyphs, e.g. "john.doe @ example . com".
# The local part deliberately forbids internal whitespace so preceding prose words
# are not swallowed into the address.
SPACED_EMAIL = re.compile(
    r"([A-Za-z0-9._%+-]{1,64})[ \t]*@[ \t]*"
    r"([A-Za-z0-9](?:[A-Za-z0-9.\- \t]{0,100}?[A-Za-z0-9])?)[ \t]*\.[ \t]*([A-Za-z]{2,24})"
)


def _repair_pdf_text(text: str) -> str:
    """Undoes the layout artifacts that most often corrupt addresses in extracted PDF text."""
    if not text:
        return ""

    # NFKC folds ligatures ('ﬁ' -> 'fi') and full-width glyphs back to ASCII
    cleaned = unicodedata.normalize("NFKC", text)

    # Soft hyphens and zero-width joiners sit invisibly inside extracted words
    cleaned = cleaned.replace("­", "").replace("​", "").replace("﻿", "")

    cleaned = HYPHEN_LINEBREAK.sub("", cleaned)
    cleaned = LINEBREAK_BEFORE_AT.sub("", cleaned)
    cleaned = LINEBREAK_AFTER_AT.sub("", cleaned)
    cleaned = LINEBREAK_BEFORE_TLD.sub("", cleaned)

    return cleaned


def _recover_spaced_emails(text: str) -> Set[str]:
    """Reconstructs addresses that the PDF laid out with whitespace between glyphs."""
    recovered: Set[str] = set()

    for match in SPACED_EMAIL.finditer(text):
        local_part, domain_part, tld = match.groups()
        domain_compact = re.sub(r"[ \t]+", "", domain_part)

        candidate = normalize_email_address(f"{local_part}@{domain_compact}.{tld}")
        if candidate and is_valid_email_syntax(candidate) and not is_false_positive_email(candidate):
            recovered.add(candidate)

    return recovered


def _extract_with_pymupdf(pdf_bytes: bytes) -> Tuple[str, List[str]]:
    """Extracts text plus 'mailto:' link annotations. Returns (text, mailto_uris)."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed")

    text_chunks: List[str] = []
    mailto_uris: List[str] = []

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise RuntimeError("PDF is password protected")

        for page in doc:
            # sort=True reflows blocks into natural reading order
            text_chunks.append(page.get_text("text", sort=True))

            for link in page.get_links():
                uri = link.get("uri") or ""
                if uri.lower().startswith("mailto:"):
                    mailto_uris.append(uri)

    return "\n".join(text_chunks), mailto_uris


def _extract_with_pdfplumber(pdf_bytes: bytes) -> Tuple[str, List[str]]:
    """Extracts text using pdfplumber's layout-aware word grouping."""
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed")

    text_chunks: List[str] = []

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(x_tolerance=1.5, y_tolerance=2.0)
            if page_text:
                text_chunks.append(page_text)

    return "\n".join(text_chunks), []


def _extract_with_pypdf(pdf_bytes: bytes) -> Tuple[str, List[str]]:
    """Extracts text using pypdf; AES-encrypted files need the 'cryptography' package."""
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed")

    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        reader.decrypt("")

    text_chunks: List[str] = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text_chunks.append(page_text)

    return "\n".join(text_chunks), []


# Ordered best-first; every engine that yields text contributes to the merged result.
PDF_ENGINES: List[Tuple[str, Callable[[bytes], Tuple[str, List[str]]]]] = [
    ("pymupdf", _extract_with_pymupdf),
    ("pdfplumber", _extract_with_pdfplumber),
    ("pypdf", _extract_with_pypdf),
]


def extract_pdf_text(pdf_bytes: bytes, pdf_url: str = "") -> Tuple[str, List[str], List[str]]:
    """
    Runs the engine chain over a PDF.

    Returns:
        tuple[str, List[str], List[str]]: (best_text, mailto_uris, engines_that_succeeded)
    """
    best_text = ""
    mailto_uris: List[str] = []
    engines_used: List[str] = []
    failures: List[str] = []

    for engine_name, engine_fn in PDF_ENGINES:
        try:
            raw_text, engine_mailtos = engine_fn(pdf_bytes)
        except Exception as exc:
            failures.append(f"{engine_name}: {exc}")
            continue

        repaired = _repair_pdf_text(raw_text)
        mailto_uris.extend(engine_mailtos)

        if repaired.strip():
            engines_used.append(engine_name)
            # Keep the richest extraction as the context source
            if len(repaired) > len(best_text):
                best_text = repaired

    if failures:
        logger.info(f"PDF engines that failed for '{pdf_url}': {'; '.join(failures)}")

    if not engines_used and not mailto_uris:
        detail = "; ".join(failures) if failures else "no engine produced any text"
        logger.error(f"Failed to parse PDF from '{pdf_url}': {detail}")

    return best_text, mailto_uris, engines_used


def extract_emails_from_pdf_bytes(
    pdf_bytes: bytes,
    pdf_url: str,
    pdf_filename: str = "",
    enable_ocr: bool = False,
    ocr_languages: str = "eng"
) -> List[EmailOccurrence]:
    """
    Extracts text from PDF binary data and returns discovered EmailOccurrence records.
    """
    if not pdf_bytes:
        return []

    combined_text, mailto_uris, engines_used = extract_pdf_text(pdf_bytes, pdf_url)
    ocr_used = False
    if enable_ocr and not combined_text.strip() and fitz is not None:
        # Optional dependency/binary: OCR failure is a soft failure, never a failed scan.
        try:
            import pytesseract
            with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
                pages = [page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png") for page in document[:20]]
            from PIL import Image
            ocr_text = "\n".join(pytesseract.image_to_string(Image.open(io.BytesIO(page)), lang=ocr_languages) for page in pages)
            if ocr_text.strip():
                combined_text = _repair_pdf_text(ocr_text)
                engines_used.append("tesseract")
                ocr_used = True
        except Exception as exc:
            logger.warning(f"OCR unavailable or failed for '{pdf_url}': {exc}")

    if not combined_text.strip() and not mailto_uris:
        return []

    title = pdf_filename or pdf_url.split("/")[-1] or "PDF Document"
    source_title = f"[PDF] {title}"

    occurrences = extract_emails_from_text(
        text=combined_text,
        source_url=pdf_url,
        source_title=source_title,
        source_type="pdf_ocr" if ocr_used else "pdf"
    )
    seen: Set[str] = {occ.email for occ in occurrences}

    # 'mailto:' link annotations are authoritative — they are stored as data, not glyphs
    for uri in mailto_uris:
        candidate = normalize_email_address(uri)
        if not candidate or candidate in seen:
            continue
        if is_valid_email_syntax(candidate) and not is_false_positive_email(candidate):
            seen.add(candidate)
            occurrences.append(EmailOccurrence(
                email=candidate,
                source_url=pdf_url,
                source_title=source_title,
                source_type="pdf",
                is_mailto=True
            ))

    # Addresses the layout spaced out, e.g. "john.doe @ example . com"
    for candidate in _recover_spaced_emails(combined_text):
        if candidate in seen:
            continue
        seen.add(candidate)
        occurrences.append(EmailOccurrence(
            email=candidate,
            source_url=pdf_url,
            source_title=source_title,
            source_type="pdf",
            is_obfuscated=True
        ))

    # Attach context to occurrences
    for occ in occurrences:
        occ.context = extract_surrounding_context(combined_text, occ.email)
        if ocr_used:
            occ.source_type = "pdf_ocr"
            occ.ocr_confidence = 0.65

    if occurrences:
        logger.info(
            f"Extracted {len(occurrences)} email(s) from PDF '{pdf_url}' "
            f"using engine(s): {', '.join(engines_used) or 'link annotations only'}"
        )
    elif not combined_text.strip():
        logger.warning(
            f"PDF '{pdf_url}' produced no extractable text — it is most likely a scanned "
            f"image PDF, which needs OCR to read."
        )

    return occurrences
