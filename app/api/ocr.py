# app/api/ocr.py

from re import template
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.ocr_service import OCRService
from app.models.ocr import OCRResponse
from app.core.logger import logger
import tempfile
from pathlib import Path
import os

router = APIRouter(prefix="/ocr", tags=["OCR"])

ocr_service = OCRService()

@router.post("/", summary="Upload a file (image or PDF) to be processed by the OCR service.")
async def ocr_upload(file: UploadFile = File(...)):
    """
    Upload a file (image or PDF) to be processed by the OCR service.
    """
    try:
        with tempfile.NamedTemporaryFile(delete=True) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = Path(temp_file.name)
            
        ocr_text = await ocr_service.process_file(temp_file_path)

        # Close and delete the temporary file
        temp_file.close()
        os.remove(temp_file_path)

        return OCRResponse(ocr_text=ocr_text)
    except Exception as e:
        logger.error(f"[OCR API] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process OCR. Please try again later.")
