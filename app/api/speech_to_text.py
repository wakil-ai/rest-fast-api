from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.logger import logger
from app.models.speech_to_text import TranscriptionResponse
from app.services.speech_to_text_service import get_speech_to_text_service

router = APIRouter(prefix="/speech-to-text", tags=["Speech-to-Text"])


@router.post("/transcribe", response_model=TranscriptionResponse)
def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form(...),
):
    """
    Transcribes audio to text using the configured speech-to-text provider.

    - **file**: The audio file to transcribe.
    - **language**: The language of the audio (e.g., 'uz-UZ', 'ru-RU', 'en-US').
    - **hints**: A comma-separated string of words or phrases to improve recognition accuracy.
    """
    try:
        speech_to_text_service = get_speech_to_text_service()

        text = speech_to_text_service.transcribe_audio(file.file, language)
        return TranscriptionResponse(text=text)
    except Exception as e:
        logger.error(f"[SpeechToTextAPI] Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to transcribe audio. Please try again later.",
        )
