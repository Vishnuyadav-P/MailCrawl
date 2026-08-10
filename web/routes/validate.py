from typing import List, Dict, Any
import csv
import io
import openpyxl
import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException, status
from pydantic import BaseModel

from src.validation.email_validator import is_valid_email_syntax, normalize_email_address
from src.validation.domain_validator import get_email_verification

router = APIRouter(prefix="/api", tags=["validate"])


class ValidationResult(BaseModel):
    original_email: str
    normalized_email: str
    is_valid_syntax: bool
    mx_status: str
    mailbox_status: str
    is_disposable: bool
    is_role_account: bool


def _extract_emails_from_text_block(text: str) -> List[str]:
    # A simple split by space/comma/newline and regex might work, but the user expects
    # each cell or row to just be an email. Let's just strip and assume it's one email per cell.
    # We will normalize it.
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
    
    filename = file.filename.lower()
    content = await file.read()
    
    emails = set()
    
    try:
        if filename.endswith(".csv"):
            text = content.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            for row in reader:
                for cell in row:
                    if cell.strip():
                        emails.update(_extract_emails_from_text_block(cell))
        elif filename.endswith((".xlsx", ".xls")):
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

    results = []
    
    # Validating can take time if many emails, use asyncio.gather for DNS checks if needed,
    # but get_email_verification uses lru_cache for mx records. Let's run it in a thread pool.
    def validate_email_list(email_list):
        res = []
        for em in email_list:
            verification = get_email_verification(em, probe_smtp=True)
            res.append(ValidationResult(
                original_email=em,
                normalized_email=em,
                is_valid_syntax=verification.syntax_valid,
                mx_status=verification.mx_status,
                mailbox_status=verification.mailbox_status,
                is_disposable=bool(verification.disposable),
                is_role_account=verification.role_account
            ))
        return res

    results = await asyncio.to_thread(validate_email_list, list(emails))
    
    return {"results": results}


class ExportRequest(BaseModel):
    results: List[ValidationResult]
    format: str

from fastapi.responses import StreamingResponse

@router.post("/validate_export")
async def validate_export(req: ExportRequest):
    if req.format not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="Invalid format")
        
    valid_results = [r for r in req.results if r.is_valid_syntax and r.mx_status == "valid" and r.mailbox_status == "valid" and not r.is_disposable]
    
    if req.format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Email", "Syntax", "MX Status", "Mailbox Status", "Disposable", "Role Account"])
        for r in req.results:
            writer.writerow([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.is_disposable, r.is_role_account])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=validation_results.csv"}
        )
    else:
        wb = openpyxl.Workbook()
        
        # Sheet 1: All Emails
        ws_all = wb.active
        ws_all.title = "All Emails"
        headers = ["Email", "Syntax", "MX Status", "Mailbox Status", "Disposable", "Role Account"]
        ws_all.append(headers)
        for r in req.results:
            ws_all.append([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.is_disposable, r.is_role_account])
            
        # Sheet 2: Validated Emails
        ws_valid = wb.create_sheet(title="Validated Emails")
        ws_valid.append(headers)
        for r in valid_results:
            ws_valid.append([r.original_email, r.is_valid_syntax, r.mx_status, r.mailbox_status, r.is_disposable, r.is_role_account])
            
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=validation_results.xlsx"}
        )
