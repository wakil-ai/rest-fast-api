# app/models/speech_to_text.py

from pydantic import BaseModel

class TranscriptionResponse(BaseModel):
    text: str
