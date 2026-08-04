"""
In-memory job registry and per-job event fan-out.

The threading contract, stated once and obeyed everywhere below:

  * A scan runs on its own worker thread. It mutates JobState only through `emit`,
    always under `_lock`.
  * HTTP handlers run on the server loop and read JobState only under the same
    `_lock`. Holding a threading.Lock on the event loop is acceptable here purely
    because no critical section performs I/O — they are list and deque appends.
  * Fan-out crosses threads via `loop.call_soon_threadsafe`. Subscriber queues carry
    a wakeup token only, never a payload; readers always pull events out of the ring
    buffer by sequence number. A slow client therefore cannot grow memory without
    bound, and a dropped wakeup cannot lose an event.
"""

import asyncio
import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from src.models.email import CanonicalEmailResult
from src.models.scan import ScanConfig, ScanError, ScanProgress, ScanStats
from src.utils.logging import logger
from web import settings

# Terminal states: the scan will not progress further without a new request.
TERMINAL_STATUSES = {"completed", "interrupted", "failed", "cancelled"}

# States that leave a usable checkpoint behind.
RESUMABLE_STATUSES = {"interrupted", "failed", "cancelled"}


class ScanCancelled(Exception):
    """
    Raised from the crawler's on_progress callback to stop a scan.

    AsyncCrawler exposes no cancel hook, but it invokes on_progress inside
    _process_url, and its own `except Exception` handler writes a checkpoint before
    re-raising. Cancelling this way is therefore resumable by construction.
    """


@dataclass
class JobState:
    """Everything known about one scan, live or just-finished."""

    scan_id: str
    search_name: str
    target_domain: str
    config: ScanConfig
    reveal_every: int
    resume: bool = False
    resumed_from: Optional[str] = None

    status: str = "queued"
    phase: str = "queued"
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[Dict[str, str]] = None
    expires_at: Optional[datetime] = None

    progress: ScanProgress = field(default_factory=ScanProgress)
    live_emails: List[Dict[str, Any]] = field(default_factory=list)
    live_email_count: int = 0
    live_truncated: bool = False
    stats: Optional[ScanStats] = None
    scan_errors: List[ScanError] = field(default_factory=list)
    results: Optional[List[CanonicalEmailResult]] = None
    crawler: Any = None

    _seq: int = 0
    _buffer: Deque[Tuple[int, str, str]] = field(
        default_factory=lambda: deque(maxlen=settings.EVENT_BUFFER_SIZE)
    )
    _subscribers: Set[asyncio.Queue] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Emission (worker thread)
    # ------------------------------------------------------------------ #

    def emit(self, event: str, payload: Dict[str, Any]) -> int:
        """
        Records an event and wakes every subscriber. Called from the worker thread.

        Returns the sequence number assigned to the event.
        """
        with self._lock:
            self._seq += 1
            seq = self._seq
            payload = {**payload, "seq": seq}
            self._apply(event, payload)
            self._buffer.append((seq, event, json.dumps(payload, default=str)))

        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._wake)
            except RuntimeError:
                # Server loop already closed; nothing left to notify.
                pass

        return seq

    def _wake(self) -> None:
        """Nudges subscribers. Runs on the server loop."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(1)
            except asyncio.QueueFull:
                # Safe to drop: the reader re-scans the buffer from its own cursor,
                # and the keepalive tick guarantees it wakes again regardless.
                pass

    def _apply(self, event: str, payload: Dict[str, Any]) -> None:
        """Folds an event into the snapshot. Caller already holds the lock."""
        if event == "progress":
            self.progress = ScanProgress.model_validate(
                {k: v for k, v in payload.items() if k in ScanProgress.model_fields}
            )
        elif event == "phase":
            self.phase = payload.get("phase", self.phase)
        elif event == "emails":
            batch = payload.get("batch", [])
            self.live_email_count += len(batch)
            room = settings.LIVE_EMAIL_CAP - len(self.live_emails)
            if room > 0:
                self.live_emails.extend(batch[:room])
            if len(batch) > max(room, 0):
                self.live_truncated = True

    # ------------------------------------------------------------------ #
    # Subscription (server loop)
    # ------------------------------------------------------------------ #

    def subscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def events_after(self, cursor: int) -> List[Tuple[int, str, str]]:
        """Returns buffered events newer than `cursor`."""
        with self._lock:
            return [entry for entry in self._buffer if entry[0] > cursor]

    def oldest_buffered_seq(self) -> int:
        """Lowest sequence still replayable, or seq+1 when the buffer is empty."""
        with self._lock:
            return self._buffer[0][0] if self._buffer else self._seq + 1

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_active(self) -> bool:
        return not self.is_terminal()

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def mark_terminal(self, status: str) -> None:
        """Sets the terminal status and starts the eviction clock."""
        self.status = status
        self.finished_at = datetime.now()
        self.expires_at = self.finished_at + timedelta(seconds=settings.JOB_TTL_SECONDS)

    @property
    def duration_seconds(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.finished_at or datetime.now()
        return round((end - self.started_at).total_seconds(), 2)

    @property
    def resumable(self) -> bool:
        return self.status in RESUMABLE_STATUSES


class JobRegistry:
    """Holds live jobs, caps concurrency, and evicts finished ones."""

    def __init__(self, max_concurrent: int = settings.MAX_CONCURRENT_SCANS):
        self._jobs: Dict[str, JobState] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent

    def get(self, scan_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(scan_id)

    def all(self) -> List[JobState]:
        with self._lock:
            return list(self._jobs.values())

    def active(self) -> List[JobState]:
        """Filters inside the lock so the whole registry is not copied first."""
        with self._lock:
            return [job for job in self._jobs.values() if job.is_active()]

    def active_for_domain(self, target_domain: str) -> Optional[JobState]:
        """Running two scans of one domain at once doubles load on someone else's server."""
        with self._lock:
            for job in self._jobs.values():
                if job.is_active() and job.target_domain == target_domain:
                    return job
        return None

    def has_capacity(self) -> bool:
        with self._lock:
            active = sum(1 for job in self._jobs.values() if job.is_active())
        return active < self._max_concurrent

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def add(self, job: JobState) -> None:
        with self._lock:
            self._jobs[job.scan_id] = job

    def remove(self, scan_id: str) -> None:
        with self._lock:
            self._jobs.pop(scan_id, None)

    def evict_expired(self) -> int:
        """
        Drops finished jobs past their TTL. Running jobs are never evicted — the scan
        is the product here, and its results are already safe on disk.
        """
        now = datetime.now()
        removed = 0

        with self._lock:
            for scan_id, job in list(self._jobs.items()):
                if job.is_terminal() and job.expires_at and job.expires_at < now:
                    del self._jobs[scan_id]
                    removed += 1

        if removed:
            logger.info(f"Evicted {removed} finished job(s) from the registry.")

        return removed

    def shutdown(self, join_timeout: float = settings.SHUTDOWN_JOIN_SECONDS) -> None:
        """Signals every running scan to stop, then waits for them to checkpoint."""
        jobs = self.active()
        for job in jobs:
            job.request_cancel()

        for job in jobs:
            if job._thread and job._thread.is_alive():
                job._thread.join(timeout=join_timeout)


registry = JobRegistry()
