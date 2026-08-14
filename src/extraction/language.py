"""Language hints and translated crawl-priority keywords.

Detection is advisory only: a page is never skipped because of its language.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterable

LANGUAGE_KEYWORDS = {
    "contact": ("contact", "kontakt", "contáctenos", "contacto", "联系我们", "联系", "संपर्क", "اتصل"),
    "support": ("support", "hilfe", "soporte", "支持", "सहायता", "الدعم"),
    "careers": ("careers", "jobs", "karriere", "empleos", "採用", "求人", "करियर", "وظائف"),
}

# Fallback when neither the html[lang] attribute nor a Content-Language header
# says anything: the script the page is written in. Ordered, and first match
# wins, so a page mixing scripts reports the first one listed here.
_SCRIPT_RANGES = (
    (r"[ऀ-ॿ]", "hi"),
    (r"[؀-ۿ]", "ar"),
    (r"[぀-ヿ]", "ja"),
    (r"[一-鿿]", "zh"),
)


class _LangParser(HTMLParser):
    lang = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "html":
            self.lang = dict(attrs).get("lang", "")

def detect_language(html: str, headers: dict | None = None) -> str:
    """Return a language hint from HTML/header/Unicode script."""
    parser = _LangParser()
    try:
        parser.feed(html[:100_000])
    except Exception:
        pass
    value = parser.lang or (headers or {}).get("content-language", "")
    if value:
        return value.split(",")[0].strip().lower().split("-")[0]
    sample = re.sub(r"<[^>]+>", " ", html[:30_000])
    for pattern, code in _SCRIPT_RANGES:
        if re.search(pattern, sample):
            return code
    return "unknown"

def priority_keywords() -> Iterable[str]:
    return (word for words in LANGUAGE_KEYWORDS.values() for word in words)
