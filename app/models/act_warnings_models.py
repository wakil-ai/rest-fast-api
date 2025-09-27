from pydantic import BaseModel, HttpUrl
from typing import List

class ActWarningURLRequest(BaseModel):
    urls: List[HttpUrl]
    concurrency: int = 10

class ActWarningResponse(BaseModel):
    with_warnings: List[HttpUrl]
    without_warnings: List[HttpUrl]