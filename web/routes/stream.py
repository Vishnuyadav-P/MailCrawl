"""
Server-sent events.

A reconnecting client sends Last-Event-ID. If that sequence is still in the job's ring
buffer it receives the exact deltas it missed; otherwise it receives a fresh snapshot,
which is a superset by construction. There is no third case, so events cannot be lost.

Duplicates are harmless: `snapshot` replaces client state, deltas append, and the
client keys email rows by address.
"""

import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from web import settings
from web.jobs.registry import JobState, registry
from web.services.serializers import job_snapshot

router = APIRouter(prefix="/api", tags=["stream"])


def _frame(event: str, data: str, seq: Optional[int] = None) -> str:
    """Formats one SSE frame."""
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {data}\n\n"


def _resume_position(request: Request, from_seq: Optional[int]) -> Optional[int]:
    """Last-Event-ID wins over the query parameter."""
    header = request.headers.get("last-event-id")
    if header:
        try:
            return int(header)
        except ValueError:
            return None
    return from_seq


async def _event_source(job: JobState, start_after: Optional[int]) -> AsyncIterator[str]:
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    job.subscribe(queue)

    try:
        yield f"retry: {settings.SSE_RETRY_MS}\n\n"

        # Decide between replaying deltas and sending a full snapshot.
        oldest = job.oldest_buffered_seq()
        if start_after is None or start_after < oldest - 1:
            snapshot = job_snapshot(job)
            cursor = snapshot["last_seq"]
            yield _frame("snapshot", json.dumps(snapshot, default=str), seq=cursor)
        else:
            cursor = start_after

        while True:
            for seq, event, data in job.events_after(cursor):
                yield _frame(event, data, seq=seq)
                cursor = seq

            # A terminal job that has been fully drained needs no open connection.
            if job.is_terminal() and cursor >= job.seq:
                return

            try:
                await asyncio.wait_for(queue.get(), timeout=settings.KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                # Also the disconnect detector: starlette only surfaces a dropped
                # client on the next write, so an idle stream must still write.
                yield ": keepalive\n\n"

    finally:
        job.unsubscribe(queue)


@router.get("/scans/{scan_id}/stream")
async def stream_scan(scan_id: str, request: Request, from_seq: Optional[int] = None):
    """
    Live event stream for a scan.

    Returns 200 even for a finished scan — a late client still gets the snapshot and
    the terminal event, then the stream closes.
    """
    job = registry.get(scan_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Unknown or evicted scan '{scan_id}'.")

    return StreamingResponse(
        _event_source(job, _resume_position(request, from_seq)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
