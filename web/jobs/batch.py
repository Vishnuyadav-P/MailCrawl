"""
Registry for the batch jobs: file validation and SignalHire crawls.

Scans have their own registry next door, built around an event ring buffer,
resume and checkpointing that neither of these jobs needs. What they do need is
the part that registry already proved necessary and these two had been missing:

  * a concurrency cap, so a burst of requests cannot open unbounded work; and
  * a TTL, so a finished job stops pinning its entire result set in memory.

Without the second, every validation and crawl the process ever ran stayed
resident for its lifetime.

Threading contract: a validation mutates its job from the event loop, but a
SignalHire crawl mutates from inside a worker thread while HTTP handlers read
from the loop. So writes append under the job lock and reads snapshot under it.
Appending is the hot path and copying is the cold one, which is the right way
round — the previous code copied the whole accumulated list on every batch,
making a run quadratic in the number of results.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from src.utils.logging import logger
from web import settings

TERMINAL_STATUSES = {"completed", "failed"}


@dataclass
class BatchJob:
    """One validation or SignalHire crawl, live or just-finished."""

    job_id: str
    kind: str                      # "validation" | "signalhire"
    label: str                     # source filename, or the company that was searched
    total: int = 0                 # 0 when the count is not known up front
    status: str = "running"
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    _results: List[Dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Wakeup queues for clients streaming this job. Held as a plain set because
    # only the event loop ever touches it.
    listeners: Set[Any] = field(default_factory=set)
    finished: bool = False

    def extend(self, rows: List[Dict[str, Any]]) -> int:
        """Appends a batch of results. Returns the new total processed count."""
        with self._lock:
            self._results.extend(rows)
            return len(self._results)

    @property
    def results(self) -> List[Dict[str, Any]]:
        """A snapshot. Copied so a reader on the loop cannot observe a mid-append list."""
        with self._lock:
            return list(self._results)

    @property
    def processed(self) -> int:
        with self._lock:
            return len(self._results)

    def mark_terminal(self, status: str) -> None:
        """Sets the final status and starts the eviction clock."""
        self.status = status
        self.finished = True
        self.finished_at = datetime.now()
        self.expires_at = self.finished_at + timedelta(seconds=settings.BATCH_JOB_TTL_SECONDS)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class BatchRegistry:
    """Holds live batch jobs of one kind, caps concurrency, evicts finished ones."""

    def __init__(self, kind: str, max_concurrent: int):
        self._kind = kind
        self._jobs: Dict[str, BatchJob] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent

    def get(self, job_id: str) -> Optional[BatchJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> List[BatchJob]:
        with self._lock:
            return list(self._jobs.values())

    def has_capacity(self) -> bool:
        with self._lock:
            active = sum(1 for job in self._jobs.values() if not job.is_terminal())
        return active < self._max_concurrent

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def add(self, job: BatchJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def remove(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def evict_expired(self) -> int:
        """
        Drops finished jobs past their TTL.

        Safe because every job writes its results to disk as it ends, and both
        history endpoints fall back to disk when a job is not resident.
        """
        now = datetime.now()
        removed = 0

        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.is_terminal() and job.expires_at and job.expires_at < now:
                    del self._jobs[job_id]
                    removed += 1

        if removed:
            logger.info(f"Evicted {removed} finished {self._kind} job(s).")

        return removed


validation_registry = BatchRegistry("validation", settings.MAX_CONCURRENT_VALIDATIONS)
signalhire_registry = BatchRegistry("signalhire", settings.MAX_CONCURRENT_SIGNALHIRE)
