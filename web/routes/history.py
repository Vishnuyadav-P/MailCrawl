"""
Scan history and health.
"""

from fastapi import APIRouter, HTTPException, status

from web.jobs.registry import registry
from web.services import store

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def get_history():
    """
    Every logged scan, newest first, with resume and load availability resolved.

    Built from a single pass over the log — scan_store would otherwise re-parse the
    whole file once for the records, once for find, and once for resumable.
    """
    history = await store.aread_history()

    active_ids = {job.scan_id for job in registry.active()}
    for row in history["records"]:
        row["active"] = row["scan_id"] in active_ids

    return history


@router.delete("/scans/{scan_id}/data", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan_data(scan_id: str):
    """Removes a scan's stored results and checkpoint. The log entry is left intact."""
    job = registry.get(scan_id)
    if job and job.is_active():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Scan '{scan_id}' is still running.",
        )

    await store.adelete_scan(scan_id)
    registry.remove(scan_id)


@router.get("/health")
async def health():
    """
    Server runtime state for the scan UI.
    """
    active = registry.active()

    return {
        "status": "ok",
        "active_scans": len(active),
        "max_concurrent": registry.max_concurrent,
        "data_dir": str(store.log_path().parent),
    }
