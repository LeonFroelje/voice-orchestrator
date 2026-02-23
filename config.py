import argparse
import os
from typing import Optional
from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    # --- MQTT Connection ---
    mqtt_host: str = Field(
        default="localhost", description="Mosquitto broker IP/Hostname"
    )
    mqtt_port: int = Field(default=1883, description="Mosquitto broker port")

    # --- Object Storage (S3 Compatible) ---
    s3_endpoint: str = Field(
        default="http://localhost:3900", description="URL to S3 storage"
    )
    s3_access_key: str = Field(default="your-access-key", description="S3 Access Key")
    s3_secret_key: SecretStr = Field(
        default="your-secret-key", description="S3 Secret Key"
    )
    s3_bucket: str = Field(default="voice-commands", description="S3 Bucket Name")

    # --- Home Assistant ---
    ha_url: str = Field(
        default="http://homeassistant.local:8123",
        description="The URL of your Home Assistant instance",
    )
    ha_token: Optional[SecretStr] = Field(
        default=None,
        description="Long-lived access token.",
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
    llm_api_key: SecretStr = Field(
        default="nop", description="API Key for LLM if auth is required"
    )

    # --- System ---
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


def get_settings() -> AppSettings:
    parser = argparse.ArgumentParser(description="Voice Assistant Orchestrator")

    parser.add_argument("--mqtt-host", help="Mosquitto broker IP/Hostname")
    parser.add_argument("--mqtt-port", type=int, help="Mosquitto broker port")
    parser.add_argument("--s3-endpoint", help="URL to S3 storage")
    parser.add_argument("--s3-access-key", help="S3 Access Key")
    parser.add_argument("--s3-secret-key", help="S3 Secret Key")
    parser.add_argument("--s3-bucket", help="S3 Bucket Name")

    parser.add_argument("--ha-url", help="Home Assistant URL")
    parser.add_argument("--ha-token", help="Home Assistant Token String")

    parser.add_argument("--llm-url", help="LLM API URL")
    parser.add_argument("--llm-model", help="LLM Model Name")

    parser.add_argument("--log-level", help="Logging Level (DEBUG, INFO)")

    args, unknown = parser.parse_known_args()
    cli_args = {k.replace("-", "_"): v for k, v in vars(args).items() if v is not None}
    return AppSettings(**cli_args)


settings = get_settings()
