from pydantic import BaseModel
from typing import Optional


class SignalHireEmployee(BaseModel):
    name: str
    title: str
    company: Optional[str] = None
