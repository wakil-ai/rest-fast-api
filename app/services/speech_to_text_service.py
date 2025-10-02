# app/services/speech_to_text_service.py

from abc import ABC, abstractmethod
from typing import List, Optional
import io

from pydub import AudioSegment

from app.core.config import settings

class SpeechToTextService(ABC):
    """
    Abstract base class for speech-to-text services.
    """
    @abstractmethod
    def transcribe_audio(
        self,
        file,
        language: str,
        hints: Optional[List[str]] = None
    ) -> str:
        """
        Transcribes the given audio file to text.
        """
        pass

class GoogleSpeechToTextService(SpeechToTextService):
    """
    Speech-to-text service using Google Cloud Speech-to-Text.
    """
    def __init__(self):
        from google.cloud import speech
        self.client = speech.SpeechClient()
        self.speech = speech

    def transcribe_audio(
        self,
        file,
        language: str,
        hints: Optional[List[str]] = None
    ) -> str:
        audio_segment = AudioSegment.from_file(file).set_frame_rate(16000).set_sample_width(2)
        buffer = io.BytesIO()
        audio_segment.export(buffer, format="wav")
        content = buffer.getvalue()

        audio = self.speech.RecognitionAudio(content=content)

        adaptation = None
        if hints:
            adaptation = self.speech.SpeechAdaptation(
                phrase_sets=[
                    self.speech.SpeechAdaptation.PhraseSet(
                        phrases=[self.speech.SpeechAdaptation.PhraseSet.Phrase(value=hint) for hint in hints]
                    )
                ]
            )

        config = self.speech.RecognitionConfig(
            encoding=self.speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=language,
            adaptation=adaptation
        )

        response = self.client.recognize(config=config, audio=audio)

        if not response.results:
            return ""

        return response.results[0].alternatives[0].transcript

class AzureSpeechToTextService(SpeechToTextService):
    """
    Speech-to-text service using Azure Cognitive Services.
    """
    def __init__(self):
        import azure.cognitiveservices.speech as speechsdk
        self.speech_config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY,
            region=settings.AZURE_SPEECH_REGION
        )
        self.speechsdk = speechsdk

    def transcribe_audio(
        self,
        file,
        language: str,
        hints: Optional[List[str]] = None
    ) -> str:
        audio_segment = AudioSegment.from_file(file).set_frame_rate(16000).set_sample_width(2)
        buffer = io.BytesIO()
        audio_segment.export(buffer, format="wav")
        content = buffer.getvalue()

        push_stream = self.speechsdk.audio.PushAudioInputStream()
        audio_config = self.speechsdk.audio.AudioConfig(stream=push_stream)
        speech_recognizer = self.speechsdk.SpeechRecognizer(
            speech_config=self.speech_config, 
            language=language,
            audio_config=audio_config
        )

        if hints:
            phrase_list_grammar = self.speechsdk.PhraseListGrammar.from_recognizer(speech_recognizer)
            for hint in hints:
                phrase_list_grammar.add_phrase(hint)

        push_stream.write(content)
        push_stream.close()

        result = speech_recognizer.recognize_once()

        if result.reason == self.speechsdk.ResultReason.RecognizedSpeech:
            return result.text
        elif result.reason == self.speechsdk.ResultReason.NoMatch:
            return ""
        elif result.reason == self.speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            raise Exception(f"Speech recognition canceled: {cancellation_details.reason} - {cancellation_details.error_details}")
        return ""

def get_speech_to_text_service() -> SpeechToTextService:
    """
    Factory function to get the speech-to-text service based on the provider setting.
    """
    if settings.SPEECH_TO_TEXT_PROVIDER == 'google':
        return GoogleSpeechToTextService()
    elif settings.SPEECH_TO_TEXT_PROVIDER == 'azure':
        return AzureSpeechToTextService()
    else:
        raise ValueError(f"Unknown speech-to-text provider: {settings.SPEECH_TO_TEXT_PROVIDER}")
