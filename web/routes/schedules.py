import asyncio

from fastapi import APIRouter, HTTPException

from src.models.scan import ScanConfig
from src.utils.urls import normalize_domain_input
from web.jobs.registry import registry
from web.jobs.runner import build_job, start_job
from web.schemas import ScheduleInput, ScheduleUpdate
from web.services import schedules, store

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("")
async def list_all():
    return await asyncio.to_thread(schedules.list_schedules)


@router.post("")
async def create(payload: ScheduleInput):
    domain, _ = normalize_domain_input(payload.target_domain)
    data = payload.model_dump()
    data["target_domain"] = domain
    return await asyncio.to_thread(schedules.create, data)


@router.patch("/{schedule_id}")
async def patch(schedule_id: str, payload: ScheduleUpdate):
    changes = payload.model_dump(exclude_unset=True)
    row = await asyncio.to_thread(schedules.update, schedule_id, changes)
    if not row:
        raise HTTPException(404, "Unknown schedule")
    return row


@router.delete("/{schedule_id}")
async def remove(schedule_id: str):
    if not await asyncio.to_thread(schedules.delete, schedule_id):
        raise HTTPException(404, "Unknown schedule")
    return {"deleted": True}


@router.post("/{schedule_id}/run")
async def run_now(schedule_id: str):
    rows = await asyncio.to_thread(schedules.list_schedules)
    row = next((r for r in rows if r["id"] == schedule_id), None)
    if not row:
        raise HTTPException(404, "Unknown schedule")

    if not registry.has_capacity():
        raise HTTPException(429, "Scan capacity is full; this scheduled run will be deferred.")

    name = row.get("search_name") or row["target_domain"]
    config = ScanConfig(target_domain=row["target_domain"], **row["scan_config"])
    job = build_job(store.new_scan_id(name), name, config, 10)
    start_job(job, asyncio.get_running_loop())
    await asyncio.to_thread(schedules.mark_started, schedule_id, job.scan_id)

    return {"schedule_id": schedule_id, "scan_id": job.scan_id}
