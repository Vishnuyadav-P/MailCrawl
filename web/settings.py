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


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
# Off by default everywhere, including the container: the server comes up with no
# credentials and stays that way until this is set. It is worth setting for anything
# reachable beyond localhost — every endpoint below serves harvested contact data,
# so an exposed unauthenticated deployment is a data leak rather than merely an
# open API.
AUTH_ENABLED: bool = _bool("MAILCRAWL_AUTH_ENABLED", False)
AUTH_USERNAME: str = os.getenv("MAILCRAWL_USER", "")
AUTH_PASSWORD: str = os.getenv("MAILCRAWL_PASSWORD", "")
AUTH_REALM: str = os.getenv("MAILCRAWL_AUTH_REALM", "MailCrawl")

# Left open so a container orchestrator can probe liveness without credentials.
# It reports counts and the data directory, never scan content.
AUTH_EXEMPT_PATHS: frozenset = frozenset({"/api/health"})


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


# --------------------------------------------------------------------------- #
# Batch jobs (file validation and SignalHire crawls)
# --------------------------------------------------------------------------- #
# These do not drive a browser like a scan does, so they are cheaper and the caps
# are correspondingly looser — but they are not free: a validation holds every
# result in memory and occupies a thread-pool slot per SMTP probe.
MAX_CONCURRENT_VALIDATIONS: int = _int("MAX_CONCURRENT_VALIDATIONS", 3)
MAX_CONCURRENT_SIGNALHIRE: int = _int("MAX_CONCURRENT_SIGNALHIRE", 3)

# How long a finished batch job stays in memory. Results are written to disk when
# the job ends, and both history endpoints fall back to disk, so eviction is
# invisible to a client.
BATCH_JOB_TTL_SECONDS: int = _int("BATCH_JOB_TTL_SECONDS", 3600)

# Hard ceiling on an uploaded validation file. The body is read in chunks and
# rejected the moment it crosses this, so an oversized upload never lands in
# memory in full.
MAX_UPLOAD_BYTES: int = _int("MAX_UPLOAD_MB", 50) * 1024 * 1024

# Cap on a client-supplied export payload. /api/validate_export and
# /api/signalhire/export build a workbook out of whatever they are sent.
MAX_EXPORT_ROWS: int = _int("MAX_EXPORT_ROWS", 100_000)
