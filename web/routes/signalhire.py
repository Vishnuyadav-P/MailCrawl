from typing import List, Dict, Any
import csv
import io
import openpyxl
import asyncio
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.models.signalhire import SignalHireEmployee
from src.crawler.signalhire_crawler import (
    crawl_signalhire_employees,
    crawl_signalhire_employees_generator,
)
from src.utils.signalhire_store import (
    new_signalhire_id,
    save_signalhire_run,
    read_signalhire_log,
    load_signalhire_results,
)
from src.utils.logging import logger

router = APIRouter(prefix="/api/signalhire", tags=["signalhire"])

# Active background SignalHire jobs dictionary
active_signalhire: Dict[str, Dict[str, Any]] = {}


class CrawlRequest(BaseModel):
    company: str


class ExportRequest(BaseModel):
    results: List[SignalHireEmployee]
    format: str


async def run_signalhire_job(crawl_id: str, company_input: str) -> None:
    """Background worker task for SignalHire profile crawling."""
    job = active_signalhire.get(crawl_id)
    if not job:
        return

    all_accumulated: List[Dict[str, Any]] = []

    def producer():
        try:
            for page_batch in crawl_signalhire_employees_generator(company_input):
                dict_batch = [emp.model_dump() for emp in page_batch]
                all_accumulated.extend(dict_batch)
                job["processed"] = len(all_accumulated)
                job["results"] = list(all_accumulated)

                payload = json.dumps({
                    "results": dict_batch,
                    "processed": len(all_accumulated)
                }) + "\n"

                for q in list(job["listeners"]):
                    try:
                        q.put_nowait(payload)
                    except Exception:
                        pass
        except Exception as exc:
            job["error"] = str(exc)
            logger.error(f"SignalHire background crawl '{crawl_id}' error: {exc}")
        finally:
            job["status"] = "completed" if not job.get("error") else "failed"
            save_signalhire_run(crawl_id, company_input, all_accumulated)
            job["finished"] = True
            for q in list(job["listeners"]):
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    await asyncio.to_thread(producer)


@router.post("/crawl")
async def crawl_signalhire(req: CrawlRequest):
    """Crawl employees associated with a company name, domain, or SignalHire URL in background."""
    company_input = req.company.strip()
    if not company_input:
        raise HTTPException(status_code=400, detail="Company name, domain, or URL is required")

    crawl_id = new_signalhire_id(company_input)

    job = {
        "id": crawl_id,
        "company_input": company_input,
        "status": "running",
        "processed": 0,
        "results": [],
        "created_at": datetime.now().isoformat(),
        "finished": False,
        "listeners": set(),
    }
    active_signalhire[crawl_id] = job

    # Launch background crawling task independent of HTTP connection
    asyncio.create_task(run_signalhire_job(crawl_id, company_input))

    async def generate_results():
        q: asyncio.Queue = asyncio.Queue()
        job["listeners"].add(q)
        try:
            yield json.dumps({"crawl_id": crawl_id, "processed": job["processed"], "results": job["results"]}) + "\n"
            while not job["finished"]:
                msg = await q.get()
                if msg is None:
                    break
                yield msg
        finally:
            job["listeners"].discard(q)

    return StreamingResponse(generate_results(), media_type="application/x-ndjson")


def _build_export_stream(results: List[SignalHireEmployee], format: str, filename_prefix: str = "signalhire_employees"):
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
    return _build_export_stream(req.results, req.format)


@router.get("/history")
async def get_signalhire_history():
    history_logs = read_signalhire_log()
    log_map = {item["id"]: item for item in history_logs}

    active_items = []
    for crawl_id, job in active_signalhire.items():
        if crawl_id not in log_map:
            active_items.append({
                "id": crawl_id,
                "company_input": job["company_input"],
                "total_employees": job["processed"],
                "created_at": job["created_at"],
                "status": job["status"],
                "has_results": True,
            })
        else:
            log_map[crawl_id]["status"] = job["status"]

    for item in history_logs:
        if "status" not in item:
            item["status"] = "completed"

    return active_items + history_logs


@router.get("/history/{crawl_id}/results")
async def get_historical_signalhire_results(crawl_id: str):
    if crawl_id in active_signalhire:
        job = active_signalhire[crawl_id]
        return {
            "id": crawl_id,
            "company_input": job["company_input"],
            "total_employees": job["processed"],
            "created_at": job["created_at"],
            "status": job["status"],
            "results": job["results"],
        }
    data = load_signalhire_results(crawl_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl record not found")
    return data


@router.get("/history/{crawl_id}/export.{format}")
async def export_historical_signalhire(crawl_id: str, format: str):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    data = load_signalhire_results(crawl_id)
    if not data and crawl_id in active_signalhire:
        job = active_signalhire[crawl_id]
        data = {"results": job["results"]}

    if not data or "results" not in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl data not found")

    results = [SignalHireEmployee(**item) for item in data["results"]]
    clean_prefix = f"signalhire_{crawl_id}"
    return _build_export_stream(results, format, filename_prefix=clean_prefix)

