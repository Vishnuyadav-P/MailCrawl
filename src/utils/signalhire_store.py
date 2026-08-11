import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading
from src.utils.config import Config
from src.utils.logging import logger

SIGNALHIRE_LOG = "signalhire_log.jsonl"
_LOG_LOCK = threading.Lock()


def data_dir() -> Path:
    path = Path(Config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return data_dir() / SIGNALHIRE_LOG


def signalhire_dir() -> Path:
    path = data_dir() / "signalhire"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_signalhire_id(company_input: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in company_input)[:30]
    return f"{stamp}__{clean_name}"


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def save_signalhire_run(crawl_id: str, company_input: str, results: List[Dict[str, Any]]) -> None:
    created_at = datetime.now().isoformat()
    record = {
        "id": crawl_id,
        "company_input": company_input,
        "total_employees": len(results),
        "created_at": created_at,
        "has_results": True,
    }
    
    # Save results JSON
    results_payload = {
        "id": crawl_id,
        "company_input": company_input,
        "total_employees": len(results),
        "created_at": created_at,
        "results": results,
    }
    res_path = signalhire_dir() / crawl_id / "results.json"
    try:
        _write_json_atomic(res_path, results_payload)
    except Exception as exc:
        logger.error(f"Could not save SignalHire results for '{crawl_id}': {exc}")

    # Append record to log
    with _LOG_LOCK:
        try:
            with log_path().open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(f"Could not append to SignalHire log: {exc}")


def read_signalhire_log() -> List[Dict[str, Any]]:
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
                        cid = record["id"]
                        if cid not in latest:
                            order.append(cid)
                        latest[cid] = record
                    except Exception:
                        continue
        except Exception as exc:
            logger.error(f"Could not read SignalHire log: {exc}")
            return []
            
    records = [latest[cid] for cid in reversed(order)]
    for r in records:
        cid = r.get("id")
        if cid:
            r["has_results"] = (signalhire_dir() / cid / "results.json").exists()
    return records


def load_signalhire_results(crawl_id: str) -> Optional[Dict[str, Any]]:
    path = signalhire_dir() / crawl_id / "results.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.error(f"Could not load SignalHire results for '{crawl_id}': {exc}")
        return None
