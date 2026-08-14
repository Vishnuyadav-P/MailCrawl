import asyncio
import csv
import io
import json
from typing import Any, List, Set

import openpyxl
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.crawler.signalhire_crawler import crawl_signalhire_employees_generator
from src.models.signalhire import SignalHireEmployee
from src.utils.logging import logger
from src.utils.signalhire_store import (
    load_signalhire_results,
    new_signalhire_id,
    read_signalhire_log,
    save_signalhire_run,
)
from web import settings
from web.jobs.batch import BatchJob, signalhire_registry

router = APIRouter(prefix="/api/signalhire", tags=["signalhire"])

# See the note in web/routes/validate.py — asyncio keeps only a weak reference to
# a running task, so it needs an anchor until it completes.
_background_tasks: Set[asyncio.Task] = set()


class CrawlRequest(BaseModel):
    company: str


class ExportRequest(BaseModel):
    results: List[SignalHireEmployee]
    format: str


def _notify(job: BatchJob, payload: Any) -> None:
    """Pushes one NDJSON line, or None as the end-of-stream token, to every listener."""
    for queue in list(job.listeners):
        try:
            queue.put_nowait(payload)
        except Exception:
            pass


async def run_signalhire_job(job: BatchJob) -> None:
    """Background worker task for SignalHire profile crawling."""
    loop = asyncio.get_running_loop()

    def producer():
        try:
            for page_batch in crawl_signalhire_employees_generator(job.label):
                dict_batch = [emp.model_dump() for emp in page_batch]
                processed = job.extend(dict_batch)

                payload = json.dumps({
                    "results": dict_batch,
                    "processed": processed,
                }) + "\n"

                # The crawl runs on a worker thread but the listener queues belong
                # to the event loop, and asyncio.Queue is not thread-safe. Hopping
                # back across is what makes this safe rather than merely lucky.
                loop.call_soon_threadsafe(_notify, job, payload)
        except Exception as exc:
            job.error = str(exc)
            logger.error(f"SignalHire background crawl '{job.job_id}' error: {exc}")

    try:
        await asyncio.to_thread(producer)
    finally:
        job.mark_terminal("failed" if job.error else "completed")
        await asyncio.to_thread(save_signalhire_run, job.job_id, job.label, job.results)
        job.finished = True
        _notify(job, None)


@router.post("/crawl")
async def crawl_signalhire(req: CrawlRequest):
    """Crawl employees associated with a company name, domain, or SignalHire URL."""
    company_input = req.company.strip()
    if not company_input:
        raise HTTPException(status_code=400, detail="Company name, domain, or URL is required")

    if not signalhire_registry.has_capacity():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"SignalHire crawl capacity is full "
                f"({signalhire_registry.max_concurrent} running). Try again shortly."
            ),
        )

    crawl_id = new_signalhire_id(company_input)
    job = BatchJob(job_id=crawl_id, kind="signalhire", label=company_input)
    signalhire_registry.add(job)

    # Launch background crawling independent of the HTTP connection.
    task = asyncio.create_task(run_signalhire_job(job))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    async def generate_results():
        q: asyncio.Queue = asyncio.Queue()
        job.listeners.add(q)
        try:
            yield json.dumps({
                "crawl_id": crawl_id,
                "processed": job.processed,
                "results": job.results,
            }) + "\n"
            while not job.finished:
                msg = await q.get()
                if msg is None:
                    break
                yield msg
        finally:
            job.listeners.discard(q)

    return StreamingResponse(generate_results(), media_type="application/x-ndjson")


def _build_export_stream(
    results: List[SignalHireEmployee],
    format: str,
    filename_prefix: str = "signalhire_employees",
):
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Job Title"])
        for r in results:
            writer.writerow([r.name, r.title])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename_prefix}.csv"}
        )
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Employees"
        headers = ["Name", "Job Title"]
        ws.append(headers)
        for r in results:
            ws.append([r.name, r.title])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename_prefix}.xlsx"}
        )


@router.post("/export")
async def export_signalhire(req: ExportRequest):
    if req.format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    if len(req.results) > settings.MAX_EXPORT_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Export is limited to {settings.MAX_EXPORT_ROWS} rows.",
        )
    return _build_export_stream(req.results, req.format)


@router.get("/history")
async def get_signalhire_history():
    history_logs = read_signalhire_log()
    log_map = {item["id"]: item for item in history_logs}

    active_items = []
    for job in signalhire_registry.all():
        if job.job_id not in log_map:
            active_items.append({
                "id": job.job_id,
                "company_input": job.label,
                "total_employees": job.processed,
                "created_at": job.created_at.isoformat(),
                "status": job.status,
                "has_results": True,
            })
        else:
            log_map[job.job_id]["status"] = job.status

    for item in history_logs:
        if "status" not in item:
            item["status"] = "completed"

    return active_items + history_logs


@router.get("/history/{crawl_id}/results")
async def get_historical_signalhire_results(crawl_id: str):
    job = signalhire_registry.get(crawl_id)
    if job:
        return {
            "id": crawl_id,
            "company_input": job.label,
            "total_employees": job.processed,
            "created_at": job.created_at.isoformat(),
            "status": job.status,
            "results": job.results,
        }
    data = load_signalhire_results(crawl_id)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Crawl record not found"
        )
    return data


@router.get("/history/{crawl_id}/export.{format}")
async def export_historical_signalhire(crawl_id: str, format: str):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")

    data = load_signalhire_results(crawl_id)
    if not data:
        job = signalhire_registry.get(crawl_id)
        if job:
            data = {"results": job.results}

    if not data or "results" not in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl data not found")

    results = [SignalHireEmployee(**item) for item in data["results"]]
    clean_prefix = f"signalhire_{crawl_id}"
    return _build_export_stream(results, format, filename_prefix=clean_prefix)
