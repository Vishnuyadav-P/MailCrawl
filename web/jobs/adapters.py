"""
Bridges between AsyncCrawler's synchronous callbacks and the job event stream.

Both adapters exist to absorb a mismatch in how the crawler reports, not to optimise:

  * on_progress fires once per URL, so a large scan would emit tens of thousands of
    events. Progress is coalesced to a fixed rate while the snapshot stays exact.
  * on_email_batch honours email_batch_size during a crawl but ignores it on resume,
    where it replays every recovered occurrence in a single call. Batches are
    re-chunked before they reach the wire.
"""

import time
from typing import List

from src.models.email import EmailOccurrence
from src.models.scan import ScanProgress
from web import settings
from web.jobs.registry import JobState, ScanCancelled


def occurrence_to_row(occ: EmailOccurrence) -> dict:
    """Trims an occurrence to the fields the live feed renders."""
    return {
        "email": occ.email,
        "source_type": occ.source_type,
        "source_title": occ.source_title or "",
        "source_url": occ.source_url,
    }


def progress_percent(progress: ScanProgress) -> int:
    """
    Crawl completion as a whole percent.

    pages_discovered grows as the crawl finds links, so this is a fraction of what is
    currently known rather than of a fixed total — the same basis the Streamlit UI used.
    """
    known = max(progress.pages_discovered, progress.pages_scanned, 1)
    return int(min(100, max(0, round(progress.pages_scanned / known * 100))))


class ProgressAdapter:
    """
    Rate-limits progress events without losing fidelity.

    Between emits the job's snapshot is still updated in place, so a client that joins
    at any moment sees current numbers; only the event stream is thinned.
    """

    def __init__(self, job: JobState, max_hz: float = settings.PROGRESS_MAX_HZ):
        self.job = job
        self.min_interval = 1.0 / max_hz if max_hz > 0 else 0.0
        self._last_emit = 0.0

    def __call__(self, progress: ScanProgress) -> None:
        # The only lever AsyncCrawler gives us for cancellation: raising here lands in
        # its own `except Exception`, which checkpoints and re-raises.
        if self.job.cancel_requested:
            raise ScanCancelled(f"Scan '{self.job.scan_id}' was cancelled.")

        now = time.monotonic()
        if self.min_interval and (now - self._last_emit) < self.min_interval:
            # Keep the snapshot exact even when the event is suppressed.
            with self.job._lock:
                self.job.progress = progress
            return

        self._last_emit = now
        self.emit(progress)

    def emit(self, progress: ScanProgress) -> None:
        """Emits unconditionally. Used for phase changes and the final tick."""
        self._last_emit = time.monotonic()
        payload = progress.model_dump()
        payload["phase"] = self.job.phase
        payload["percent"] = progress_percent(progress)
        self.job.emit("progress", payload)

    def flush(self) -> None:
        """Forces a trailing emit so the last state is never left unsent."""
        self.emit(self.job.progress)


class EmailBatchAdapter:
    """Chunks discovered addresses into wire-sized events."""

    def __init__(self, job: JobState, max_per_event: int = settings.MAX_EMAILS_PER_EVENT):
        self.job = job
        self.max_per_event = max(1, max_per_event)

    def __call__(self, batch: List[EmailOccurrence]) -> None:
        if not batch:
            return

        rows = [occurrence_to_row(occ) for occ in batch]

        for start in range(0, len(rows), self.max_per_event):
            chunk = rows[start:start + self.max_per_event]
            self.job.emit("emails", {
                "batch": chunk,
                "total": self.job.live_email_count + len(chunk),
            })
