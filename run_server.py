#!/usr/bin/env python3
"""
Launcher for the web UI, for local development.

    python run_server.py [--port 8000] [--host 127.0.0.1] [--reload]

Host and port fall back to $HOST and $PORT. If --port is already taken, the launcher
tries the next port up until it finds one free — unless $PORT was set or --strict-port
was passed, in which case a taken port is an error.

Containers should run `uvicorn web.server:app` directly (see Dockerfile) and set
DATA_DIR explicitly; this script's job is the convenience of neither.

DATA_DIR is pinned to the project root before anything under src/ is imported.
src.utils.config reads it at import time and defaults to the RELATIVE path "data",
so launching from another directory would silently give the server a different scan
history than running from the project root does.
"""

import argparse
import importlib
import os
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Must happen before any `src.*` import.
os.environ.setdefault("DATA_DIR", str(PROJECT_ROOT / "data"))
sys.path.insert(0, str(PROJECT_ROOT))


def find_open_port(host: str, port: int, max_tries: int = 20) -> int:
    """Finds the first free port at or after `port`, trying one at a time."""
    for candidate in range(port, port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate

    raise SystemExit(
        f"No free port found in range {port}-{port + max_tries - 1} on {host}."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MailCrawl web UI.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Reload on code changes (development)")
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="Fail if the port is taken instead of trying the next one up",
    )
    args = parser.parse_args()

    uvicorn = importlib.import_module("uvicorn")

    from src.utils.config import Config

    resolved = Path(Config.DATA_DIR).resolve()
    expected = (PROJECT_ROOT / "data").resolve()
    if resolved != expected and "DATA_DIR" not in os.environ:
        raise SystemExit(
            f"DATA_DIR resolved to {resolved}, expected {expected}. "
            f"Run this from the project root or set DATA_DIR explicitly."
        )

    # A platform that injects $PORT expects the app on exactly that port; silently
    # moving up one would leave it unreachable behind the platform's router.
    if args.strict_port or "PORT" in os.environ:
        port = args.port
    else:
        port = find_open_port(args.host, args.port)
        if port != args.port:
            print(f"  Port {args.port} is in use — switched to {port}.")

    print(f"  MailCrawl  ->  http://{args.host}:{port}")
    print(f"  Scan data: {resolved}\n")

    uvicorn.run(
        "web.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
