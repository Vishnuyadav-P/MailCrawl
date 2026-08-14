"""
Structured logging module.
"""

import logging
import sys
from pathlib import Path


def setup_logger(name: str = "MailCrawl") -> logging.Logger:
    """Configures a structured console logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


logger = setup_logger()


def create_scan_file_logger(scan_id: str, target_domain: str) -> tuple[logging.Logger, Path]:
    """Creates a per-scan file logger with domain and timestamp in its filename."""
    from src.utils.scan_store import get_scan_log_filename, scan_dir

    log_file_name = get_scan_log_filename(scan_id, target_domain)
    log_file_path = scan_dir(scan_id) / log_file_name

    scan_logger = logging.getLogger(f"scan.{scan_id}")
    scan_logger.setLevel(logging.INFO)

    resolved_path = str(log_file_path.resolve())
    has_handler = any(
        isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == resolved_path
        for h in scan_logger.handlers
    )

    if not has_handler:
        handler = logging.FileHandler(log_file_path, encoding="utf-8")
        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        scan_logger.addHandler(handler)

    return scan_logger, log_file_path


def cleanup_scan_logger(scan_id: str) -> None:
    """Closes and removes the per-scan file logger to free file descriptors."""
    name = f"scan.{scan_id}"
    scan_logger = logging.getLogger(name)
    for handler in list(scan_logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        scan_logger.removeHandler(handler)
    # Remove from logging's internal manager so the logger can be garbage-collected
    manager = logging.Logger.manager
    if hasattr(manager, 'loggerDict') and name in manager.loggerDict:
        del manager.loggerDict[name]
