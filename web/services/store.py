"""
Thread-safe, loop-safe facade over src.utils.scan_store.

Two problems this solves that the underlying module does not:

1. `append_scan_log` is a bare open(...,'a') + write, and `read_scan_log` silently
   skips any line it cannot parse. With several scan threads writing at once an
   interleaved line is invisible data loss rather than an error, so every write goes
   through one process-wide lock.

2. Every function here touches the filesystem, and `read_scan_log` re-parses the whole
   JSONL on each call. Called directly from an async handler they would stall the event
   loop, so the async wrappers push them onto a thread.
"""

import asyncio
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.scan import ScanRecord
from src.utils import scan_store

# Guards the append-only log against interleaved writes from concurrent scan threads.
_LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Blocking primitives — safe to call from a scan worker thread
# --------------------------------------------------------------------------- #

def append_scan_log(record: ScanRecord) -> None:
    """Appends a scan record to the shared log under a process-wide lock."""
    with _LOG_LOCK:
        scan_store.append_scan_log(record)


def read_scan_log() -> List[ScanRecord]:
    """Reads the full scan log, newest first."""
    with _LOG_LOCK:
        return scan_store.read_scan_log()


def log_path() -> Path:
    return scan_store.log_path()


def save_results(scan_id: str, results: List[Any], stats: Any = None, config: Any = None, crawl_graph: Any = None) -> Path:
    return scan_store.save_results(scan_id, results, stats, config, crawl_graph)


def load_results(scan_id: str) -> Optional[Dict[str, Any]]:
    return scan_store.load_results(scan_id)


def load_checkpoint(scan_id: str) -> Optional[Dict[str, Any]]:
    return scan_store.load_checkpoint(scan_id)


def clear_checkpoint(scan_id: str) -> None:
    scan_store.clear_checkpoint(scan_id)


def checkpoint_exists(scan_id: str) -> bool:
    return scan_store.checkpoint_path(scan_id).exists()


def results_exist(scan_id: str) -> bool:
    return (scan_store.scans_dir() / scan_id / scan_store.RESULTS_FILENAME).exists()


def delete_scan(scan_id: str) -> None:
    scan_store.delete_scan(scan_id)


def new_scan_id(search_name: str) -> str:
    return scan_store.new_scan_id(search_name)


def read_history() -> Dict[str, Any]:
    """
    Builds the whole history view from a single pass over the log.

    scan_store exposes read_scan_log, find_scan_record and resumable_scans as three
    separate calls that each re-parse the file; doing it once here keeps GET /history
    to one read instead of three.
    """
    records = read_scan_log()

    rows: List[Dict[str, Any]] = []
    resumable_count = 0

    for record in records:
        resumable = (
            record.status in ("running", "interrupted", "failed", "cancelled")
            and checkpoint_exists(record.scan_id)
        )
        if resumable:
            resumable_count += 1

        rows.append({
            **record.model_dump(),
            "resumable": resumable,
            "has_results": results_exist(record.scan_id) or checkpoint_exists(record.scan_id),
        })

    return {
        "log_path": str(log_path()),
        "resumable_count": resumable_count,
        "records": rows,
    }


def find_scan_record(scan_id: str) -> Optional[ScanRecord]:
    for record in read_scan_log():
        if record.scan_id == scan_id:
            return record
    return None


# --------------------------------------------------------------------------- #
# Async wrappers — use these from request handlers
# --------------------------------------------------------------------------- #

async def aread_history() -> Dict[str, Any]:
    return await asyncio.to_thread(read_history)


async def aload_results(scan_id: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(load_results, scan_id)


async def aload_checkpoint(scan_id: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(load_checkpoint, scan_id)


async def afind_scan_record(scan_id: str) -> Optional[ScanRecord]:
    return await asyncio.to_thread(find_scan_record, scan_id)


async def adelete_scan(scan_id: str) -> None:
    await asyncio.to_thread(delete_scan, scan_id)


async def acheckpoint_exists(scan_id: str) -> bool:
    return await asyncio.to_thread(checkpoint_exists, scan_id)
