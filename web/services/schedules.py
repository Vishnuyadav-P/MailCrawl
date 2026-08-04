"""Persistent, single-process schedule store and due-run dispatcher."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo
from croniter import croniter
from src.utils.config import Config

_LOCK = threading.Lock()
def _path() -> Path:
    p = Path(Config.DATA_DIR) / "schedules.json"; p.parent.mkdir(parents=True, exist_ok=True); return p
def _read() -> list[dict[str, Any]]:
    try: return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return []
def _write(rows):
    p=_path(); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(p)
def _next(cron: str, tz: str, base=None) -> str:
    now = base or datetime.now(ZoneInfo(tz)); return croniter(cron, now).get_next(datetime).astimezone(timezone.utc).isoformat()
def list_schedules():
    with _LOCK: return _read()
def create(payload: dict):
    with _LOCK:
        rows=_read(); row={**payload, "id":uuid4().hex, "created_at":datetime.now(timezone.utc).isoformat(), "last_run_at":None, "last_scan_id":None}
        row["next_run_at"]=_next(row["cron"], row["timezone"]) if row.get("enabled", True) else None; rows.append(row); _write(rows); return row
def update(schedule_id: str, changes: dict):
    with _LOCK:
        rows=_read()
        for row in rows:
            if row["id"] == schedule_id:
                row.update({k:v for k,v in changes.items() if v is not None})
                row["next_run_at"]=_next(row["cron"], row["timezone"]) if row.get("enabled") else None; _write(rows); return row
    return None
def delete(schedule_id: str) -> bool:
    with _LOCK:
        rows=_read(); remaining=[r for r in rows if r["id"] != schedule_id]
        if len(rows)==len(remaining): return False
        _write(remaining); return True
def due():
    now=datetime.now(timezone.utc)
    return [r for r in list_schedules() if r.get("enabled") and r.get("next_run_at") and datetime.fromisoformat(r["next_run_at"]) <= now]
def mark_started(schedule_id: str, scan_id: str):
    with _LOCK:
        rows=_read()
        for row in rows:
            if row["id"] == schedule_id:
                row["last_run_at"]=datetime.now(timezone.utc).isoformat(); row["last_scan_id"]=scan_id; row["next_run_at"]=_next(row["cron"], row["timezone"]); _write(rows); return
