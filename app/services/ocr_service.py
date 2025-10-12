# app/services/ocr_service.py

import requests
from typing import Optional, IO
from app.core.config import settings

class OCRService:
    def __init__(self):
        self.base_url = settings.OCR_API_URL

    def process_file(self, file: IO, filename: str) -> dict:
        """
        Sends the file to the OCR API and returns the parsed JSON response.
        Expects the external OCR API to accept a multipart/form-data POST at /ocr/.
        """
        url = f"{self.base_url.rstrip('/')}/ocr/"
        files = {"file": (filename, file)}
        resp = requests.post(url, files=files, timeout=120)
        resp.raise_for_status()
        return resp.json()


def get_ocr_service() -> OCRService:
    return OCRService()
