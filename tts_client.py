import requests
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TTSClient:
    def __init__(self, tts_url: str):
        # Automatically append the correct OpenAI-compatible endpoint path
        # This allows you to just pass "http://localhost:8080" from your config
        self.tts_url = tts_url

    def generate_audio(
        self, text: str, voice: Optional[str] = None, speed: float = 1.0
    ) -> Optional[str]:
        """
        Generates audio from text and returns it as a Base64 encoded string.
        """
        try:
            logger.info(f"TTS text: {text}")

            # Construct the OpenAI-compatible payload
            payload = {
                "input": text,
                "response_format": "wav",  # We use WAV here to avoid MP3 compression overhead before base64
            }

            # If the orchestrator specifies a voice (e.g., "de_DE-kerstin-low"), inject it.
            # Otherwise, the API falls back to the default "de_DE-thorsten-high".
            if voice:
                payload["voice"] = voice

            response = requests.post(self.tts_url, json=payload)
            response.raise_for_status()

            # Encode binary audio to Base64 string so it fits in JSON
            audio_b64 = base64.b64encode(response.content).decode("utf-8")
            return audio_b64

        except requests.exceptions.RequestException as e:
            logger.error(f"TTS Request failed: {e}")
            # This is crucial for debugging: prints the exact error from FastAPI (e.g., if a voice download fails)
            if e.response is not None:
                logger.error(f"API Error Details: {e.response.text}")
            return None

        except Exception as e:
            logger.error(f"TTS Generation failed: {e}")
            return None
