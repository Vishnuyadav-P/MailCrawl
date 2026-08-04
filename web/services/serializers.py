"""
JobState -> JSON.

The snapshot built here is served both by GET /api/scans/{id} and as the SSE
`snapshot` event, so a polling client and a streaming client can never see different
shapes of the same scan.
"""

from typing import Any, Dict

from web.jobs.adapters import progress_percent
from web.jobs.registry import JobState


def _iso(value) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def job_snapshot(job: JobState) -> Dict[str, Any]:
    """The complete current state of a scan."""
    with job._lock:
        progress = job.progress.model_dump()
        live_emails = list(job.live_emails)
        live_count = job.live_email_count
        live_truncated = job.live_truncated
        seq = job._seq

    progress["percent"] = progress_percent(job.progress)

    return {
        "scan_id": job.scan_id,
        "search_name": job.search_name,
        "target_domain": job.target_domain,
        "status": job.status,
        "phase": job.phase,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "duration_seconds": job.duration_seconds,
        "resumed": job.resume,
        "resumed_from": job.resumed_from,
        "progress": progress,
        "stats": job.stats.model_dump() if job.stats else None,
        "config": job.config.model_dump(),
        "emails_live": live_emails,
        "live_count": live_count,
        "live_truncated": live_truncated,
        "result_count": len(job.results) if job.results is not None else None,
        "errors_count": len(job.scan_errors),
        "error": job.error,
        "resumable": job.resumable,
        "last_seq": seq,
    }


def job_accepted(job: JobState) -> Dict[str, Any]:
    """The 202 body returned when a scan is started or resumed."""
    return {
        "scan_id": job.scan_id,
        "search_name": job.search_name,
        "target_domain": job.target_domain,
        "status": job.status,
        "resumed": job.resume,
        "resumed_from": job.resumed_from,
        "created_at": _iso(job.created_at),
        "stream_url": f"/api/scans/{job.scan_id}/stream",
        "snapshot_url": f"/api/scans/{job.scan_id}",
    }
