from typing import List, Dict, Any
import csv
import io
import openpyxl
import asyncio
import json
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

router = APIRouter(prefix="/api", tags=["validate"])


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


@router.post("/validate_file")
async def validate_file(file: UploadFile = File(...)):
    """Upload a CSV or Excel file containing emails to validate them."""
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

    # Validating can take time if many emails, let me stream results in batches of 10
    async def generate_results():
        email_list = list(emails)
        batch_size = 10
        all_accumulated = []
        
        # Yield total count first for the progress bar
        yield json.dumps({"total": len(email_list), "validation_id": val_id}) + "\n"
        
        for i in range(0, len(email_list), batch_size):
            batch = email_list[i:i+batch_size]
            
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
            yield json.dumps({"results": dict_batch}) + "\n"
            
        # Save complete validation run to disk
        save_validation_run(val_id, filename, all_accumulated)

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
    return read_validation_log()


@router.get("/validate_history/{validation_id}/results")
async def get_historical_validation_results(validation_id: str):
    data = load_validation_results(validation_id)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Validation record not found")
    return data


@router.get("/validate_history/{validation_id}/export.{format}")
async def export_historical_validation(validation_id: str, format: str):
    if format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
    data = load_validation_results(validation_id)
    if not data or "results" not in data:
        raise HTTPException(status_code=404, detail="Validation data not found")
    
    results = [ValidationResult(**item) for item in data["results"]]
    clean_prefix = f"validation_{validation_id}"
    return _build_export_stream(results, format, filename_prefix=clean_prefix)
