# app/api/ocr.py

from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.ocr_service import OCRService
from app.models.ocr import OCRResponse
from app.core.logger import logger

router = APIRouter(prefix="/ocr", tags=["OCR"])

ocr_service = OCRService()

@router.post("/", response_model=OCRResponse)
def ocr_upload(file: UploadFile = File(...)):
    """
    Upload a file (image or PDF) to be processed by the OCR service.
    """
    try:
        result = ocr_service.process_file(file.file, file.filename)
        return OCRResponse(**result)
    except Exception as e:
        logger.error(f"[OCR API] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process OCR. Please try again later.")
