from typing import List, Dict, Any
import csv
import io
import openpyxl
import asyncio
import json
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.validation.email_validator import is_valid_email_syntax, normalize_email_address
from src.validation.domain_validator import get_email_verification
from src.utils.validation_store import (
    new_validation_id,
    save_validation_run,
    read_validation_log,
    load_validation_results,
)
from src.utils.logging import logger

router = APIRouter(prefix="/api", tags=["validate"])

# Active background validation jobs dictionary
active_validations: Dict[str, Dict[str, Any]] = {}


class ValidationResult(BaseModel):
    original_email: str
    normalized_email: str
    is_valid_syntax: bool
    mx_status: str
    mailbox_status: str
    reason: str


def _extract_emails_from_text_block(text: str) -> List[str]:
    emails = []
    for part in str(text).replace(',', ' ').replace(';', ' ').split():
        norm = normalize_email_address(part)
        if norm:
            emails.append(norm)
    return emails


async def run_validation_job(val_id: str, filename: str, email_list: List[str]) -> None:
    """Background worker task for email batch validation."""
    job = active_validations.get(val_id)
    if not job:
        return

    batch_size = 10
    all_accumulated: List[Dict[str, Any]] = []

    try:
        for i in range(0, len(email_list), batch_size):
            batch = email_list[i:i + batch_size]

            def process_batch(b):
                res = []
                for em in b:
                    verification = get_email_verification(em, probe_smtp=True)
                    res.append(ValidationResult(
                        original_email=em,
                        normalized_email=em,
                        is_valid_syntax=verification.syntax_valid,
                        mx_status=verification.mx_status,
                        mailbox_status=verification.mailbox_status,
                        reason=verification.provider_notes if verification.provider_notes else ""
                    ))
                return res

            batch_results = await asyncio.to_thread(process_batch, batch)
            dict_batch = [r.model_dump() for r in batch_results]
            all_accumulated.extend(dict_batch)

            job["processed"] = len(all_accumulated)
            job["results"] = list(all_accumulated)

            payload = json.dumps({
                "results": dict_batch,
                "processed": len(all_accumulated),
                "total": len(email_list)
            }) + "\n"

            for q in list(job["listeners"]):
                try:
                    q.put_nowait(payload)
                except Exception:
                    pass

        job["status"] = "completed"
        save_validation_run(val_id, filename, all_accumulated)
    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        logger.error(f"Background validation '{val_id}' error: {exc}")
        if all_accumulated:
            save_validation_run(val_id, filename, all_accumulated)
    finally:
        job["finished"] = True
        for q in list(job["listeners"]):
            try:
                q.put_nowait(None)
            except Exception:
                pass


@router.post("/validate_file")
async def validate_file(file: UploadFile = File(...)):
    """Upload a CSV or Excel file containing emails to validate them in background."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    filename = file.filename
    content = await file.read()

    emails = set()

    try:
        fname_lower = filename.lower()
        if fname_lower.endswith(".csv"):
            text = content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            for row in reader:
                for cell in row:
                    if cell.strip():
                        emails.update(_extract_emails_from_text_block(cell))
        elif fname_lower.endswith((".xlsx", ".xls")):
            wb = openpyxl.load_workbook(filename=io.BytesIO(content), data_only=True)
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell and str(cell).strip():
                            emails.update(_extract_emails_from_text_block(str(cell)))
        else:
            raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")

    val_id = new_validation_id(filename)
    email_list = list(emails)

    job = {
        "id": val_id,
        "filename": filename,
        "status": "running",
        "total": len(email_list),
        "processed": 0,
        "results": [],
        "created_at": datetime.now().isoformat(),
        "finished": False,
        "listeners": set(),
    }
    active_validations[val_id] = job

    # Launch background execution task independent of HTTP stream
    asyncio.create_task(run_validation_job(val_id, filename, email_list))

    async def generate_results():
        q: asyncio.Queue = asyncio.Queue()
        job["listeners"].add(q)
        try:
            yield json.dumps({"total": len(email_list), "validation_id": val_id, "processed": job["processed"], "results": job["results"]}) + "\n"
            while not job["finished"]:
                msg = await q.get()
                if msg is None:
                    break
                yield msg
        finally:
            job["listeners"].discard(q)

    return StreamingResponse(generate_results(), media_type="application/x-ndjson")


class ExportRequest(BaseModel):
    results: List[ValidationResult]
    format: str


def _build_export_stream(results: List[ValidationResult], format: str, filename_prefix: str = "validation_results"):
    valid_results = [r for r in results if r.is_valid_syntax and r.mx_status == "valid" and r.mailbox_status == "valid"]

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Email", "Syntax", "MX Status", "Mailbox Status", "Remarks"])
        for r in results:
            writer.writerow([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.reason])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename_prefix}.csv"}
        )
    else:
        wb = openpyxl.Workbook()

        # Sheet 1: All Emails
        ws_all = wb.active
        ws_all.title = "All Emails"
        headers = ["Email", "Syntax", "MX Status", "Mailbox Status", "Remarks"]
        ws_all.append(headers)
        for r in results:
            ws_all.append([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.reason])

        # Sheet 2: Validated Emails
        ws_valid = wb.create_sheet(title="Validated Emails")
        ws_valid.append(headers)
        for r in valid_results:
            ws_valid.append([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.reason])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename_prefix}.xlsx"}
        )


@router.post("/validate_export")
async def validate_export(req: ExportRequest):
    if req.format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    return _build_export_stream(req.results, req.format)


@router.get("/validate_history")
async def get_validate_history():
    history_logs = read_validation_log()
    log_map = {item["id"]: item for item in history_logs}

    active_items = []
    for val_id, job in active_validations.items():
        if val_id not in log_map:
            active_items.append({
                "id": val_id,
                "filename": job["filename"],
                "total_emails": job["total"],
                "created_at": job["created_at"],
                "status": job["status"],
                "has_results": True,
            })
        else:
            log_map[val_id]["status"] = job["status"]

    for item in history_logs:
        if "status" not in item:
            item["status"] = "completed"

    return active_items + history_logs


@router.get("/validate_history/{validation_id}/results")
async def get_historical_validation_results(validation_id: str):
    if validation_id in active_validations:
        job = active_validations[validation_id]
        return {
            "id": validation_id,
            "filename": job["filename"],
            "total_emails": job["total"],
            "created_at": job["created_at"],
            "status": job["status"],
            "results": job["results"],
        }
    data = load_validation_results(validation_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Validation record not found")
    return data


@router.get("/validate_history/{validation_id}/export.{format}")
async def export_historical_validation(validation_id: str, format: str):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    data = load_validation_results(validation_id)
    if not data and validation_id in active_validations:
        job = active_validations[validation_id]
        data = {"results": job["results"]}

    if not data or "results" not in data:
        raise HTTPException(status_code=404, detail="Validation data not found")

    results = [ValidationResult(**item) for item in data["results"]]
    clean_prefix = f"validation_{validation_id}"
    return _build_export_stream(results, format, filename_prefix=clean_prefix)

