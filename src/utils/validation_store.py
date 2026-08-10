import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading
from src.utils.config import Config
from src.utils.logging import logger

VALIDATION_LOG = "validation_log.jsonl"
_LOG_LOCK = threading.Lock()


def data_dir() -> Path:
    path = Path(Config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return data_dir() / VALIDATION_LOG


def validations_dir() -> Path:
    path = data_dir() / "validations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_validation_id(filename: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in filename)[:30]
    return f"{stamp}__{clean_name}"


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def save_validation_run(validation_id: str, filename: str, results: List[Dict[str, Any]]) -> None:
    created_at = datetime.now().isoformat()
    record = {
        "id": validation_id,
        "filename": filename,
        "total_emails": len(results),
        "created_at": created_at,
        "has_results": True,
    }
    
    # 1. Save detailed results JSON
    results_payload = {
        "id": validation_id,
        "filename": filename,
        "total_emails": len(results),
        "created_at": created_at,
        "results": results,
    }
    res_path = validations_dir() / validation_id / "results.json"
    try:
        _write_json_atomic(res_path, results_payload)
    except Exception as exc:
        logger.error(f"Could not save validation results for '{validation_id}': {exc}")

    # 2. Append record to log
    with _LOG_LOCK:
        try:
            with log_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(f"Could not append to the validation log: {exc}")


def read_validation_log() -> List[Dict[str, Any]]:
    path = log_path()
    if not path.exists():
        return []
        
    latest: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    
    with _LOG_LOCK:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        vid = record["id"]
                        if vid not in latest:
                            order.append(vid)
                        latest[vid] = record
                    except Exception:
                        continue
        except Exception as exc:
            logger.error(f"Could not read the validation log: {exc}")
            return []
            
    records = [latest[vid] for vid in reversed(order)]
    for r in records:
        vid = r.get("id")
        if vid:
            r["has_results"] = (validations_dir() / vid / "results.json").exists()
    return records


def load_validation_results(validation_id: str) -> Optional[Dict[str, Any]]:
    path = validations_dir() / validation_id / "results.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Could not load validation results for '{validation_id}': {exc}")
        return None
