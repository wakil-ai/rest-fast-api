# app/models/ocr.py

from pydantic import BaseModel
from typing import List

class PageText(BaseModel):
    page: int
    lines: List[str]

class OCRResponse(BaseModel):
    file_name: str
    pages_processed: int
    text_by_page: List[PageText]
    combined_text: str
