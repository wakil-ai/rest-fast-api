# app/models/ocr.py

from pydantic import BaseModel, Field

class OCRResponse(BaseModel):
    ocr_text: str = Field(..., description="OCR text in markdown format")
