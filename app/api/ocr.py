# app/api/ocr.py

from re import template
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from app.services.ocr_service import OCRService
from app.models.ocr import OCRResponse
from app.core.logger import logger
import tempfile
from pathlib import Path
import os

class OCRRequest(BaseModel):
    url: str = None

router = APIRouter(prefix="/ocr", tags=["OCR"])

ocr_service = OCRService()

@router.post("/upload", summary="Upload a file (image or PDF) to be processed by the OCR service.")
async def ocr_upload(file: UploadFile = File(...)):
    """
    Upload a file (image or PDF) to be processed by the OCR service.
    """
    try:
        # Use proper temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name

        try:
            ocr_text = await ocr_service.process_file(temp_file_path)
            return OCRResponse(ocr_text=ocr_text)
        finally:
            # Always clean up the temporary file
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    except Exception as e:
        logger.error(f"[OCR API] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process OCR. Please try again later.")


@router.post("/url", summary="Process a document from URL for OCR.")
async def ocr_url(request: OCRRequest):
    """
    Process a document from URL for OCR.
    """
    try:
        if not request.url:
            raise HTTPException(status_code=400, detail="URL is required")

        ocr_text = await ocr_service.process_url(request.url)
        return OCRResponse(ocr_text=ocr_text)
    except Exception as e:
        logger.error(f"[OCR API] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process OCR. Please try again later.")
