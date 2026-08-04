"""
Utils package initialization.
"""

from src.utils.config import Config
from src.utils.logging import logger
from src.utils.urls import (
    canonicalize_url,
    get_url_priority,
    is_same_registered_domain,
    normalize_domain_input,
)

__all__ = [
    "logger",
    "Config",
    "normalize_domain_input",
    "is_same_registered_domain",
    "canonicalize_url",
    "get_url_priority",
]
