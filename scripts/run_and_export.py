#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def is_terminal_status(status: str) -> bool:
    return status in {"completed", "failed", "interrupted"}


def wait_for_server(url: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with request.urlopen(f"{url}/api/health", timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Server did not become healthy in time")


def call_json(url: str, payload: dict | None = None, method: str = "GET") -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=600) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def poll_scan(scan_id: str, url: str, timeout: int = 1800) -> dict:
    deadline = time.time() + timeout
    last_state = {}
    while time.time() < deadline:
        state = call_json(f"{url}/api/scans/{scan_id}")
        last_state = state
        status = str(state.get("status", "")).lower()
        if is_terminal_status(status):
            return state
        time.sleep(10)
    raise RuntimeError(f"Scan did not finish in time. Last state: {json.dumps(last_state)}")


def download_export(url: str, scan_id: str, output_path: Path) -> None:
    req = request.Request(f"{url}/api/scans/{scan_id}/export.csv")
    with request.urlopen(req, timeout=600) as response:
        output_path.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the app, run a scan, and export the results")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--search-name", default=None)
    parser.add_argument("--output", default=str(ROOT / "artifacts" / "results.csv"))
    parser.add_argument("--host", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("CI"):
        print("This helper is intended for CI or a headless run; local execution is possible too.")

    server = subprocess.Popen(
        [sys.executable, "run_server.py", "--host", "127.0.0.1", "--port", "8000", "--strict-port"],
        cwd=str(ROOT),
        env={**os.environ, "DATA_DIR": str(DATA_DIR)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        wait_for_server(args.host)
        payload = {
            "domain": args.domain,
            "search_name": args.search_name,
            "reveal_every": 10,
            "config": {
                "max_depth": 0,
                "timeout": 10,
                "concurrent_requests": 6,
                "include_pdfs": True,
                "js_rendering": "automatic",
                "only_target_domain": True,
                "include_subdomains": True,
                "include_external_sources": True,
                "include_wellknown_sources": True,
                "extra_source_urls": [],
                "respect_robots": True,
                "checkpoint_every_pages": 50,
                "enable_pdf_ocr": False,
                "ocr_languages": "eng",
                "enable_smtp_verification": False,
            },
        }
        started = call_json(f"{args.host}/api/scans", payload=payload, method="POST")
        scan_id = started["scan_id"]
        state = poll_scan(scan_id, args.host, timeout=args.timeout)
        if not is_terminal_status(str(state.get("status", "")).lower()):
            raise RuntimeError(f"Scan did not finish successfully: {state}")
        download_export(args.host, scan_id, output_path)
        print(json.dumps({"scan_id": scan_id, "output": str(output_path), "status": state.get("status")}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
