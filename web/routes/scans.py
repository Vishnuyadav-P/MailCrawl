"""
Scan lifecycle: start, resume, cancel, snapshot.
"""

import asyncio

from fastapi import APIRouter, HTTPException, status

from src.models.scan import ScanConfig
from src.utils.urls import normalize_domain_input
from web.jobs.registry import registry
from web.jobs.runner import build_job, start_job
from web.schemas import ResumeScanRequest, StartScanRequest
from web.services import store
from web.services.serializers import job_accepted, job_snapshot

router = APIRouter(prefix="/api", tags=["scans"])


def _guard_capacity(target_domain: str) -> None:
    """Rejects a scan that would collide with a running one or exceed the cap."""
    existing = registry.active_for_domain(target_domain)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A scan for {target_domain} is already running (scan_id: {existing.scan_id}).",
        )

    if not registry.has_capacity():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"{registry.max_concurrent} scans are already running. "
                f"Wait for one to finish, or cancel it."
            ),
            headers={"Retry-After": "30"},
        )


@router.post("/scans", status_code=status.HTTP_202_ACCEPTED)
async def start_scan(payload: StartScanRequest):
    """Starts a new scan and returns immediately with its id and stream URL."""
    try:
        registered_domain, _ = normalize_domain_input(payload.domain)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid domain input: {err}",
        ) from err

    _guard_capacity(registered_domain)

    search_name = (payload.search_name or "").strip() or registered_domain
    config = ScanConfig(target_domain=registered_domain, **payload.config.model_dump())

    job = build_job(
        scan_id=store.new_scan_id(search_name),
        search_name=search_name,
        config=config,
        reveal_every=payload.reveal_every,
    )
    start_job(job, asyncio.get_running_loop())

    return job_accepted(job)


@router.post("/scans/{scan_id}/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_scan(scan_id: str, payload: ResumeScanRequest | None = None):
    """
    Resumes an interrupted scan from its checkpoint.

    The scan continues under the settings it started with, not whatever the UI
    currently shows, so a resumed crawl cannot silently change scope halfway through.
    """
    payload = payload or ResumeScanRequest()

    existing = registry.get(scan_id)
    if existing and existing.is_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scan '{scan_id}' is already running.",
        )

    checkpoint = await store.aload_checkpoint(scan_id)
    if not checkpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scan '{scan_id}' has no checkpoint left to resume from.",
        )

    try:
        config = ScanConfig.model_validate(checkpoint["config"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The saved settings for '{scan_id}' could not be read: {exc}",
        ) from exc

    _guard_capacity(config.target_domain)

    record = await store.afind_scan_record(scan_id)
    search_name = record.search_name if record else scan_id

    job = build_job(
        scan_id=scan_id,
        search_name=search_name,
        config=config,
        reveal_every=payload.reveal_every,
        resume=True,
        resumed_from=record.finished_at if record else None,
    )
    start_job(job, asyncio.get_running_loop())

    body = job_accepted(job)
    body["checkpoint"] = {
        "pages_scanned": (checkpoint.get("stats") or {}).get("pages_scanned", 0),
        "queued_urls": len(checkpoint.get("queue", [])),
        "saved_at": checkpoint.get("saved_at"),
    }
    return body


@router.post("/scans/{scan_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_scan(scan_id: str):
    """
    Asks a running scan to stop.

    The crawler checkpoints on its way out, so a cancelled scan stays resumable.
    """
    job = registry.get(scan_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' is not running.")

    if job.is_terminal():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scan '{scan_id}' already finished with status '{job.status}'.",
        )

    job.request_cancel()
    return {"scan_id": scan_id, "status": "cancelling"}


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: str):
    """Full snapshot of a scan — the polling equivalent of the SSE stream."""
    job = registry.get(scan_id)
    if job:
        return job_snapshot(job)

    # Evicted from memory but still on disk.
    record = await store.afind_scan_record(scan_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Unknown scan '{scan_id}'.")

    return {
        "scan_id": record.scan_id,
        "search_name": record.search_name,
        "target_domain": record.target_domain,
        "status": record.status,
        "phase": "archived",
        "created_at": record.started_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at or None,
        "duration_seconds": record.duration_seconds,
        "resumed": bool(record.resumed_from),
        "resumed_from": record.resumed_from,
        "progress": None,
        "stats": None,
        "config": None,
        "emails_live": [],
        "live_count": record.unique_emails,
        "live_truncated": False,
        "result_count": record.unique_emails,
        "errors_count": 0,
        "error": None,
        "resumable": await store.acheckpoint_exists(scan_id),
        "archived": True,
        "last_seq": 0,
    }
