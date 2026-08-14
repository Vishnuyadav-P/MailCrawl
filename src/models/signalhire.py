from typing import Optional

from pydantic import BaseModel


class SignalHireEmployee(BaseModel):
    name: str
    title: str
    company: Optional[str] = None
