from typing import List, Dict, Any
import csv
import io
import openpyxl
import asyncio
import json
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

router = APIRouter(prefix="/api/signalhire", tags=["signalhire"])


class CrawlRequest(BaseModel):
    company: str


class ExportRequest(BaseModel):
    results: List[SignalHireEmployee]
    format: str


@router.post("/crawl")
async def crawl_signalhire(req: CrawlRequest):
    """Crawl employees associated with a company name, domain, or SignalHire URL."""
    company_input = req.company.strip()
    if not company_input:
        raise HTTPException(status_code=400, detail="Company name, domain, or URL is required")
        
    crawl_id = new_signalhire_id(company_input)

    async def generate_results():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        def producer():
            try:
                for page_batch in crawl_signalhire_employees_generator(company_input):
                    loop.call_soon_threadsafe(queue.put_nowait, page_batch)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # Offload crawl thread
        asyncio.create_task(asyncio.to_thread(producer))
        
        all_accumulated = []
        
        while True:
            batch = await queue.get()
            if batch is None:
                break
                
            dict_batch = [emp.model_dump() for emp in batch]
            all_accumulated.extend(dict_batch)
            yield json.dumps({"results": dict_batch}) + "\n"
            
        # Save complete run to disk
        save_signalhire_run(crawl_id, company_input, all_accumulated)

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
    return read_signalhire_log()


@router.get("/history/{crawl_id}/results")
async def get_historical_signalhire_results(crawl_id: str):
    data = load_signalhire_results(crawl_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl record not found")
    return data


@router.get("/history/{crawl_id}/export.{format}")
async def export_historical_signalhire(crawl_id: str, format: str):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    data = load_signalhire_results(crawl_id)
    if not data or "results" not in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl data not found")
    
    results = [SignalHireEmployee(**item) for item in data["results"]]
    clean_prefix = f"signalhire_{crawl_id}"
    return _build_export_stream(results, format, filename_prefix=clean_prefix)
