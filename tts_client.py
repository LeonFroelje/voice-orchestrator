import requests
import base64
import logging

logger = logging.getLogger(__name__)


class TTSClient:
    def __init__(self, tts_url: str):
        self.tts_url = tts_url

    def generate_audio(self, text: str) -> str:
        """
        Generates audio from text and returns it as a Base64 encoded string.
        """
        try:
            # Adjust parameters based on your specific TTS engine (e.g., Piper, Coqui)
            # This example assumes a simple GET request that returns raw WAV bytes
            logger.info(f"TTS text: {text}")
            response = requests.post(self.tts_url, json={"text": text})
            response.raise_for_status()

            # Encode binary audio to Base64 string so it fits in JSON
            audio_b64 = base64.b64encode(response.content).decode("utf-8")
            return audio_b64

        except Exception as e:
            logger.error(f"TTS Generation failed: {e}")
            return None
