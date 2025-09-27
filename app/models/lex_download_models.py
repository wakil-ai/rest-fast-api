from pydantic import BaseModel, AnyHttpUrl, Field
from typing import List, Optional

class LexDownloadURLRequest(BaseModel):
    urls: List[AnyHttpUrl]
    out_dir: str = "data/WORD_OUTPUT"
    concurrency: int = 6
    prefer_doc: bool = True
    delay_ms: int = 0

class DLResult(BaseModel):
    saved: bool
    filename: Optional[str] = None

class DLResponse(BaseModel):
    saved: int
    total: int