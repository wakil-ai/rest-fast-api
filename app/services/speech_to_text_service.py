# app/services/speech_to_text_service.py

from abc import ABC, abstractmethod
from typing import List, Optional
import io
import os
import requests

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

class AzureRESTSpeechToTextService(SpeechToTextService):
    """
    Speech-to-text service using Azure Cognitive Services REST API.
    This implementation works reliably in Docker containers.
    """
    def __init__(self):
        self.subscription_key = settings.AZURE_SPEECH_KEY
        self.region = settings.AZURE_SPEECH_REGION
        self.base_url = f"https://{self.region}.stt.speech.microsoft.com"

    def transcribe_audio(
        self,
        file,
        language: str,
        hints: Optional[List[str]] = None
    ) -> str:
        try:
            # Convert audio to the required format
            audio_segment = AudioSegment.from_file(file).set_frame_rate(16000).set_sample_width(2).set_channels(1)
            buffer = io.BytesIO()
            audio_segment.export(buffer, format="wav")
            audio_data = buffer.getvalue()

            # Prepare headers
            headers = {
                'Ocp-Apim-Subscription-Key': self.subscription_key,
                'Content-Type': 'audio/wav; codecs=audio/pcm; samplerate=16000',
                'Accept': 'application/json',
            }

            # Build URL with parameters
            url = f"{self.base_url}/speech/recognition/conversation/cognitiveservices/v1"
            params = {
                'language': language,
                'format': 'detailed'
            }

            # Make the request
            response = requests.post(
                url,
                headers=headers,
                params=params,
                data=audio_data,
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('RecognitionStatus') == 'Success':
                    # Get the best result
                    nbest = result.get('NBest', [])
                    if nbest:
                        return nbest[0].get('Display', '').strip()
                    elif result.get('DisplayText'):
                        return result.get('DisplayText').strip()
                return ""
            else:
                raise Exception(f"Azure Speech API error: {response.status_code} - {response.text}")

        except Exception as e:
            raise Exception(f"Azure REST Speech-to-Text failed: {str(e)}")

def _is_running_in_docker() -> bool:
    """
    Detect if the application is running inside a Docker container.
    """
    # Check for Docker-specific files/environment
    return (
        os.path.exists('/.dockerenv') or
        os.path.exists('/proc/1/cgroup') and 'docker' in open('/proc/1/cgroup').read() or
        os.environ.get('PYTHONPATH') == '/app'
    )

def get_speech_to_text_service() -> SpeechToTextService:
    """
    Factory function to get the speech-to-text service based on the provider setting.
    Automatically uses REST API for Azure when running in Docker.
    """
    if settings.SPEECH_TO_TEXT_PROVIDER == 'google':
        return GoogleSpeechToTextService()
    elif settings.SPEECH_TO_TEXT_PROVIDER == 'azure':
        # Use REST API implementation in Docker, SDK implementation locally
        if _is_running_in_docker():
            return AzureRESTSpeechToTextService()
        else:
            return AzureSpeechToTextService()
    else:
        raise ValueError(f"Unknown speech-to-text provider: {settings.SPEECH_TO_TEXT_PROVIDER}")
