import argparse
import os
from typing import Optional
from pydantic import SecretStr, Field, AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    # --- Home Assistant ---
    ha_url: str = Field(
        default="http://homeassistant.local:8123",
        description="The URL of your Home Assistant instance",
    )
    ha_token: Optional[SecretStr] = Field(
        default=None,
        description="Long-lived access token. If not set, checks ha_token_file.",
    )
    ha_token_file: Optional[str] = Field(
        default=None,
        description="Path to a file containing the HA token (useful for Docker secrets)",
    )
    speaker_id_protocol: str = Field(default="http")
    speaker_id_host: str = Field(default="localhost")
    speaker_id_port: int = Field(default=8001)
    # --- Spotify ---

    spotify_client_id: str = Field(
        default=None, description="Client ID for Spotify web api"
    )
    spotify_client_secret: str = Field(
        default=None, description="Client secret for Spotify web api"
    )
    spotify_redirect_url: str = Field(
        default="https://127.0.0.1", description="Redirect url for Spotify web api"
    )

    # --- LLM Service ---
    llm_url: str = Field(
        default="http://localhost:11434/v1",
        description="Base URL for the LLM API (Ollama/Llama.cpp)",
    )
    llm_model: str = Field(
        default="qwen3:1.7b",
        description="The specific model tag to use for inference",
    )
    llm_auth_required: bool = Field(
        default=False,
        description="If True, the LLM client will send the configured API Key",
    )
    llm_api_key: SecretStr = Field(
        default="nop", description="API Key for LLM if auth is required"
    )
    # --- Transcription service ---
    whisper_host: str = Field(
        default="localhost", description="Hostname or IP of the Whisper-Live server"
    )
    whisper_protocol: str = Field(
        default="http",
        description="The protocol to use for transcription (http or https)",
    )
    whisper_port: int = Field(
        default=9090, description="Port of the Whisper-Live server"
    )
    whisper_model: str = Field(
        default="large-v3",
        description="Whisper model size (tiny, base, small, medium, large-v2, etc.)",
    )
    language: str = Field(
        default="de", description="Language code for STT (e.g., 'en', 'de', 'es')"
    )

    # --- TTS Service ---
    tts_url: str = Field(
        default="http://localhost:5000/v1/audio/speech",
        description="Endpoint for the Text-to-Speech service",
    )
    tts_voice: str = Field(
        default="de_DE-thorsten-high",
        description="Voice ID to use for TTS generation",
    )

    # --- System ---
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Pydantic Config: Tells it to read from .env files automatically
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    def model_post_init(self, __context):
        """
        Post-initialization hook to handle the TOKEN_FILE logic.
        This runs after CLI args and Env vars are merged.
        """
        # If Token is missing, but a File path is provided, read the file
        if self.ha_token is None and self.ha_token_file:
            try:
                with open(self.ha_token_file, "r") as f:
                    self.ha_token = SecretStr(f.read().strip())
            except IOError as e:
                raise ValueError(
                    f"Could not read HA Token file at {self.ha_token_file}: {e}"
                )

        # Validation: We must have a token by now
        if self.ha_token is None:
            raise ValueError(
                "No Home Assistant Token provided. Set HA_TOKEN or HA_TOKEN_FILE."
            )


def get_settings() -> AppSettings:
    """
    Parses CLI arguments first, then initializes Settings.
    Pydantic precedence: Init Kwargs (CLI) > Environment Vars > Defaults
    """
    parser = argparse.ArgumentParser(description="Voice Assistant Orchestrator")

    # Add arguments for every field you want controllable via CLI
    # We use hyphens for CLI (e.g. --ha-url) which map to underscores in Pydantic (ha_url)
    parser.add_argument("--ha-url", help="Home Assistant URL")
    parser.add_argument("--ha-token", help="Home Assistant Token String")
    parser.add_argument("--ha-token-file", help="Path to HA Token File")

    parser.add_argument("--spotify-client-id", help="Spotify client id for web api")
    parser.add_argument(
        "--spotify-client-secret", help="Spotify client secret for web api"
    )
    parser.add_argument(
        "--spotify-redirect-url", help="Redirect url for Spotify web api"
    )

    parser.add_argument("--llm-url", help="LLM API URL")
    parser.add_argument("--llm-model", help="LLM Model Name")
    parser.add_argument(
        "--llm-auth-required", type=bool, help="Set to True if LLM needs Auth"
    )

    parser.add_argument("--tts-url", help="TTS API URL")

    parser.add_argument("--host", help="Server Host")
    parser.add_argument("--port", type=int, help="Server Port")
    parser.add_argument("--log-level", help="Logging Level (DEBUG, INFO)")

    args, unknown = parser.parse_known_args()

    # Create a dictionary of only the arguments that were actually provided via CLI
    # This is crucial: If we passed None, it would overwrite the Env Var!
    cli_args = {k.replace("-", "_"): v for k, v in vars(args).items() if v is not None}

    # Initialize Settings
    # 1. Pydantic loads .env and System Environment variables
    # 2. We overwrite those with cli_args
    return AppSettings(**cli_args)


# Create a global instance for easy import
settings = get_settings()
