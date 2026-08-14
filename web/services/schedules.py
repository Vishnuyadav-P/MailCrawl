"""Persistent, single-process schedule store and due-run dispatcher."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from src.utils.config import Config

# Every public function here reads, mutates and rewrites the whole file, so the
# lock covers that sequence rather than any single operation. Nothing below holds
# it across a call to another locked function.
_LOCK = threading.Lock()


def _path() -> Path:
    path = Path(Config.DATA_DIR) / "schedules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read() -> List[Dict[str, Any]]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _write(rows: List[Dict[str, Any]]) -> None:
    """Writes through a temp file so an interrupted write cannot truncate the store."""
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _next(cron: str, tz: str, base: Optional[datetime] = None) -> str:
    """Next fire time as a UTC ISO string. Cron and tz are validated at the schema."""
    now = base or datetime.now(ZoneInfo(tz))
    return croniter(cron, now).get_next(datetime).astimezone(timezone.utc).isoformat()


def list_schedules() -> List[Dict[str, Any]]:
    with _LOCK:
        return _read()


def create(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        rows = _read()
        row = {
            **payload,
            "id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_run_at": None,
            "last_scan_id": None,
        }
        row["next_run_at"] = (
            _next(row["cron"], row["timezone"]) if row.get("enabled", True) else None
        )
        rows.append(row)
        _write(rows)
        return row


def update(schedule_id: str, changes: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _LOCK:
        rows = _read()
        for row in rows:
            if row["id"] != schedule_id:
                continue

            row.update({k: v for k, v in changes.items() if v is not None})
            # Default to True rather than False: a row written before `enabled`
            # existed would otherwise silently disable itself on its first edit.
            row["next_run_at"] = (
                _next(row["cron"], row["timezone"]) if row.get("enabled", True) else None
            )
            _write(rows)
            return row
    return None


def delete(schedule_id: str) -> bool:
    with _LOCK:
        rows = _read()
        remaining = [r for r in rows if r["id"] != schedule_id]
        if len(rows) == len(remaining):
            return False
        _write(remaining)
        return True


def due() -> List[Dict[str, Any]]:
    """Schedules whose next run time has passed. Calls list_schedules, so takes no lock."""
    now = datetime.now(timezone.utc)
    return [
        r for r in list_schedules()
        if r.get("enabled")
        and r.get("next_run_at")
        and datetime.fromisoformat(r["next_run_at"]) <= now
    ]


def mark_started(schedule_id: str, scan_id: str) -> None:
    with _LOCK:
        rows = _read()
        for row in rows:
            if row["id"] != schedule_id:
                continue

            row["last_run_at"] = datetime.now(timezone.utc).isoformat()
            row["last_scan_id"] = scan_id
            row["next_run_at"] = _next(row["cron"], row["timezone"])
            _write(rows)
            return
