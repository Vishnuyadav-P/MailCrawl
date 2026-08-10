from pydantic import BaseModel
import asyncio
from src.validation.domain_validator import get_email_verification

class ValidationResult(BaseModel):
    original_email: str
    normalized_email: str
    is_valid_syntax: bool
    mx_status: str
    is_disposable: bool
    is_role_account: bool

def test():
    verification = get_email_verification("invalid-email")
    print("Verification:", verification)
    res = ValidationResult(
        original_email="invalid-email",
        normalized_email="invalid-email",
        is_valid_syntax=verification.syntax_valid,
        mx_status=verification.mx_status,
        is_disposable=verification.disposable,
        is_role_account=verification.role_account
    )
    print("ValidationResult:", res)

test()
