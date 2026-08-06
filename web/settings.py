"""
Tuning knobs for the web server.

Every value is overridable by environment variable so a deployment can be tightened
without editing code.
"""

import os

from dotenv import load_dotenv

# Read at import time, so .env has to be loaded before the first _int() call below.
# src.utils.config also calls this, but only if it happens to have been imported
# first — which it is today purely because src/utils/__init__.py re-exports Config.
# Calling it here is idempotent and does not depend on that.
load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Each scan launches its own Chromium (src/crawler/crawler.py starts a PlaywrightFetcher
# per crawl), so the browser is what caps concurrency here, not the event loop.
MAX_CONCURRENT_SCANS: int = _int("MAX_CONCURRENT_SCANS", 1)

# How long a finished job stays in memory. Results are on disk either way, and
# GET /results falls back to disk, so eviction loses nothing.
JOB_TTL_SECONDS: int = _int("JOB_TTL_SECONDS", 3600)
CLEANUP_INTERVAL_SECONDS: int = _int("CLEANUP_INTERVAL_SECONDS", 60)

# AsyncCrawler notifies progress once per URL. Without coalescing a 50k-page scan
# would push 50k SSE events; the snapshot is still updated in place between emits.
PROGRESS_MAX_HZ: float = _float("PROGRESS_MAX_HZ", 5.0)

# Ring buffer of emitted events, used to replay deltas to a reconnecting client.
# A client further behind than this gets a full snapshot instead.
EVENT_BUFFER_SIZE: int = _int("EVENT_BUFFER_SIZE", 2000)

# Live feed rows held in memory per job. Past this the feed reports truncation and
# the client is expected to read the full set from /results.
LIVE_EMAIL_CAP: int = _int("LIVE_EMAIL_CAP", 5000)

# On resume the crawler replays every recovered occurrence in a single callback,
# ignoring email_batch_size, so batches have to be re-chunked before they are emitted.
MAX_EMAILS_PER_EVENT: int = _int("MAX_EMAILS_PER_EVENT", 200)

# Doubles as the disconnect detector: starlette only surfaces a dropped client on
# the next write, so an idle stream would otherwise leak a subscriber forever.
KEEPALIVE_SECONDS: float = _float("KEEPALIVE_SECONDS", 15.0)

# Reconnect delay advertised to EventSource.
SSE_RETRY_MS: int = _int("SSE_RETRY_MS", 2000)

# Results paging.
DEFAULT_PAGE_SIZE: int = _int("DEFAULT_PAGE_SIZE", 10)
MAX_PAGE_SIZE: int = _int("MAX_PAGE_SIZE", 2000)

# Grace period for in-flight scans to checkpoint themselves on server shutdown.
SHUTDOWN_JOIN_SECONDS: float = _float("SHUTDOWN_JOIN_SECONDS", 30.0)
