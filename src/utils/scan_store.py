"""
Persistent scan history and crawl checkpoints.

Two things live on disk under the data directory:

  scan_log.jsonl              append-only log, one JSON line per scan
  scans/<scan_id>/            per-scan directory
      results.json            canonical results of a finished scan
      checkpoint.json         crawl state, so an interrupted scan can resume

A scan_id is '<timestamp>__<slugified search name>', which sorts chronologically
and stays readable in a directory listing.

Every write goes to a temporary file first and is then moved into place, so a scan
killed mid-write leaves the previous checkpoint intact rather than a truncated one.
"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models.scan import ScanRecord
from src.utils.config import Config
from src.utils.logging import logger

LOG_FILENAME = "scan_log.jsonl"
RESULTS_FILENAME = "results.json"
CHECKPOINT_FILENAME = "checkpoint.json"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def data_dir() -> Path:
    """Returns the root data directory, creating it if needed."""
    path = Path(Config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def scans_dir() -> Path:
    """Returns the directory holding per-scan data."""
    path = data_dir() / "scans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scan_dir(scan_id: str) -> Path:
    """Returns the directory for one scan, creating it if needed."""
    path = scans_dir() / scan_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(value: str) -> str:
    """Reduces a search name to a filesystem-safe slug."""
    slug = _SLUG_STRIP.sub("-", (value or "").strip().lower()).strip("-")
    return slug[:60] or "scan"


def new_scan_id(search_name: str, when: Optional[datetime] = None) -> str:
    """Builds a chronologically sortable, human-readable scan identifier."""
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}__{slugify(search_name)}"


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Writes JSON via a temporary file so an interrupted write cannot corrupt the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp_path, path)


# --------------------------------------------------------------------------- #
# Scan log
# --------------------------------------------------------------------------- #

def log_path() -> Path:
    """Returns the path of the append-only scan log."""
    return data_dir() / LOG_FILENAME


def append_scan_log(record: ScanRecord) -> None:
    """Appends a scan record to the log."""
    try:
        with log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.model_dump(), ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.error(f"Could not append to the scan log: {exc}")


def read_scan_log() -> List[ScanRecord]:
    """
    Reads every log entry, newest first.

    Later entries for the same scan_id supersede earlier ones, so a scan logged as
    'running' and later as 'completed' appears once with its final status.
    """
    path = log_path()
    if not path.exists():
        return []

    latest: Dict[str, ScanRecord] = {}
    order: List[str] = []

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = ScanRecord.model_validate(json.loads(line))
                except Exception:
                    # A partially written final line must not hide the whole history
                    continue

                if record.scan_id not in latest:
                    order.append(record.scan_id)
                latest[record.scan_id] = record
    except Exception as exc:
        logger.error(f"Could not read the scan log: {exc}")
        return []

    return [latest[scan_id] for scan_id in reversed(order)]


def find_scan_record(scan_id: str) -> Optional[ScanRecord]:
    """Returns the newest log entry for a scan, or None."""
    for record in read_scan_log():
        if record.scan_id == scan_id:
            return record
    return None


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

def save_results(scan_id: str, results: List[Any], stats: Any = None, config: Any = None, crawl_graph: Any = None) -> Path:
    """Persists the canonical results of a finished scan."""
    payload = {
        "scan_id": scan_id,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "results": [item.model_dump() for item in results],
        "stats": stats.model_dump() if stats is not None else None,
        "config": config.model_dump() if config is not None else None,
        "crawl_graph": crawl_graph,
    }

    path = scan_dir(scan_id) / RESULTS_FILENAME
    _write_json_atomic(path, payload)
    return path


def load_results(scan_id: str) -> Optional[Dict[str, Any]]:
    """Loads a saved result payload, or None when it is missing or unreadable."""
    path = scans_dir() / scan_id / RESULTS_FILENAME
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Could not read results for scan '{scan_id}': {exc}")
        return None


# --------------------------------------------------------------------------- #
# Checkpoints
# --------------------------------------------------------------------------- #

def checkpoint_path(scan_id: str) -> Path:
    """Returns the checkpoint path for a scan."""
    return scans_dir() / scan_id / CHECKPOINT_FILENAME


def save_checkpoint(scan_id: str, state: Dict[str, Any]) -> None:
    """Persists crawl state so the scan can resume from this point."""
    state = {**state, "saved_at": datetime.now().isoformat(timespec="seconds")}
    try:
        _write_json_atomic(checkpoint_path(scan_id), state)
    except Exception as exc:
        logger.error(f"Could not write a checkpoint for scan '{scan_id}': {exc}")


def load_checkpoint(scan_id: str) -> Optional[Dict[str, Any]]:
    """Loads crawl state for a scan, or None when there is nothing to resume."""
    path = checkpoint_path(scan_id)
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Checkpoint for scan '{scan_id}' is unreadable: {exc}")
        return None


def clear_checkpoint(scan_id: str) -> None:
    """Removes the checkpoint once a scan has finished."""
    path = checkpoint_path(scan_id)
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        logger.warning(f"Could not remove the checkpoint for scan '{scan_id}': {exc}")


def resumable_scans() -> List[ScanRecord]:
    """Returns logged scans that stopped early and still have a checkpoint on disk."""
    return [
        record for record in read_scan_log()
        if record.status in ("running", "interrupted", "failed")
        and checkpoint_path(record.scan_id).exists()
    ]


def delete_scan(scan_id: str) -> None:
    """Removes a scan's stored results and checkpoint. The log entry is left intact."""
    target = scans_dir() / scan_id
    try:
        if target.exists():
            shutil.rmtree(target)
    except Exception as exc:
        logger.warning(f"Could not delete stored data for scan '{scan_id}': {exc}")
