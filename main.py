from fastapi import FastAPI, HTTPException, UploadFile, File, Form
import httpx
import wave
import io
import logging
from pydantic import BaseModel
from contextlib import asynccontextmanager
import os
from ha_client import HomeAssistantClient  # <--- Import the new class
from tool_handler import execute_tool
from tts_client import TTSClient
from fastapi.responses import Response, JSONResponse
import requests
from config import settings
import json
from openai import OpenAI
from typing import List, Optional, Dict, Any
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_FILE = os.path.join(BASE_DIR, "tools.json")


def load_tools():
    if not os.path.exists(TOOLS_FILE):
        raise FileNotFoundError(f"Could not find {TOOLS_FILE}")

    with open(TOOLS_FILE, "r") as f:
        return json.load(f)


ha_tools_definitions = load_tools()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Logic
    logger.info(f"Starting Orchestrator on {settings.host}:{settings.port}")
    logger.info(f"Connected to Home Assistant at {settings.ha_url}")
    logger.info(f"Using LLM: {settings.llm_model} at {settings.llm_url}")

    if settings.llm_auth_required and settings.llm_api_key.get_secret_value() == "nop":
        logger.warning("LLM Auth is enabled but API Key is default 'nop'.")

    yield


logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Orchestrator")


app = FastAPI(lifespan=lifespan)
ha_client = HomeAssistantClient(
    base_url=settings.ha_url, token=settings.ha_token.get_secret_value()
)

service_context = {"ha": ha_client}

tts_client = TTSClient(tts_url=settings.tts_url)

llm_client = OpenAI(
    base_url=settings.llm_url, api_key=settings.llm_api_key.get_secret_value()
)

ha_headers = {
    "Authorization": f"Bearer {settings.ha_token.get_secret_value()}",
    "Content-Type": "application/json",
}

active_sessions: Dict[str, float] = {}

# --- New Endpoints for Volume Ducking ---

@app.post("/event/wakeword")
async def handle_wakeword(room: str = Form(...)):
    """
    Called when a satellite detects a wakeword. 
    Lowers volume of the media_player in that room.
    """
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"
    logger.info(f"Wakeword detected in {room}. Checking {entity_id} for ducking.")

    try:
        # Get current state from HA
        state = ha_client.get_state(entity_id)
        if state and state.get("state") == "playing":
            current_volume = state.get("attributes", {}).get("volume_level", 0.5)
            
            # Store the original volume
            active_sessions[room] = current_volume
            
            # Lower volume (ducking) to 20% of current or a fixed low value
            duck_volume = max(0.1, current_volume * 0.5)
            ha_client.call_service("media_player", "volume_set", {
                "entity_id": entity_id,
                "volume_level": duck_volume
            })
            return {"status": "ducked", "previous_volume": current_volume}
            
    except Exception as e:
        logger.error(f"Failed to duck volume for {room}: {e}")
    
    return {"status": "no_action"}


@app.post("/event/finished")
async def handle_finished(room: str = Form(...)):
    """
    Called when the assistant is done speaking or listening.
    Restores the original volume.
    """
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"
    
    if room in active_sessions:
        original_volume = active_sessions.pop(room)
        try:
            ha_client.call_service("media_player", "volume_set", {
                "entity_id": entity_id,
                "volume_level": original_volume
            })
            logger.info(f"Restored volume for {room} to {original_volume}")
            return {"status": "restored"}
        except Exception as e:
            logger.error(f"Failed to restore volume for {room}: {e}")

    return {"status": "no_session"}


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "config": {
            "ha_url": settings.ha_url,
            "llm_model": settings.llm_model,
            "tts_voice": settings.tts_voice,
        },
    }


class Action(BaseModel):
    type: str
    payload: Dict[str, Any]


class OrchestratorResponse(BaseModel):
    status: str
    transcription: Optional[str] = None
    response_text: Optional[str] = None
    audio_b64: Optional[str] = None
    actions: List[Action] = []


def get_ha_context_by_label(label="voice-assistant"):
    """
    Uses HA Template API to find entities with a specific label.
    This is much more efficient than fetching all states.
    """
    template = (
        "{% for entity in label_entities('"
        + label
        + "') %}{{ entity }}, {{ states(entity) }}, {{ state_attr(entity, 'friendly_name') }}|{% endfor %}"
    )

    try:
        url = f"{settings.ha_url}/api/template"
        response = requests.post(url, headers=ha_headers, json={"template": template})
        response.raise_for_status()

        # Parse the pipe-separated response
        raw_data = response.text.strip().split("|")
        context_lines = []
        for line in raw_data:
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 3:
                    eid = parts[0].strip()
                    state = parts[1].strip()
                    name = parts[2].strip()
                    context_lines.append(
                        f'{{"entity_id": "{eid}", "friendly_name": "{name}", "state": {state}}}'
                    )

        return "\n".join(context_lines)
    except Exception as e:
        print(f"Error fetching HA context: {e}")
        return "No devices found."


def call_ha_service(domain, service, data):
    url = f"{settings.ha_url}/api/services/{domain}/{service}"
    requests.post(url, headers=ha_headers, json=data)


def transcribe_audio_api(audio_bytes: bytes) -> str:
    """Sends WAV audio bytes to an OpenAI-compatible transcription endpoint."""
    logger.info("Sending audio to Transcription API...")

    # The audio_bytes are already a valid WAV file from the satellite
    buffer = io.BytesIO(audio_bytes)

    try:
        url = f"{settings.whisper_protocol}://{settings.whisper_host}:{settings.whisper_port}/v1/audio/transcriptions"

        files = {"file": ("audio.wav", buffer, "audio/wav")}
        data = {"model": settings.whisper_model}

        headers = {}
        if getattr(settings, "api_token", None):
            headers["Authorization"] = f"Bearer {settings.api_token.get_secret_value()}"

        response = requests.post(
            url, files=files, data=data, headers=headers, timeout=15
        )

        if response.ok:
            return response.json().get("text", "")
        else:
            logger.error(f"STT API Error: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Failed to connect to STT API: {e}")

    return ""


@app.post("/process", response_model=OrchestratorResponse)
async def process_command(
    file: UploadFile = File(...), room: Optional[str] = Form(None)
):
    logger.info(f"Received audio command from room: {room}")

    audio_bytes = await file.read()

    # The OpenAI client requires a file-like object with a filename
    buffer = io.BytesIO(audio_bytes)
    buffer.name = "audio.wav"
    speaker_id = "Unbekannt"
    try:
        async with httpx.AsyncClient() as client:
            # We send the audio bytes as a file to the identification service
            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
            logger.info(
                f"{settings.speaker_id_protocol}://{settings.speaker_id_host}:{settings.speaker_id_port}/identify"
            )
            id_response = await client.post(
                f"{settings.speaker_id_protocol}://{settings.speaker_id_host}:{settings.speaker_id_port}/identify",
                files=files,
            )

            if id_response.status_code == 200:
                result = id_response.json()
                speaker_id = result.get("speaker_id", "Unbekannt")
                logger.info(
                    f"Speaker identified: {speaker_id} (Score: {result.get('score')})"
                )
    except Exception as e:
        logger.error(f"Speaker ID Error: {e}")
    try:
        transcribed_text = transcribe_audio_api(audio_bytes)
        logger.info(f"Transcribed Text: {transcribed_text}")
    except Exception as e:
        logger.error(f"STT Error: {e}")
        return OrchestratorResponse(
            status="error", response_text="Fehler bei der Spracherkennung."
        )

    if not transcribed_text.strip():
        return OrchestratorResponse(
            status="empty",
            audio_b64="",
            response_text="Empty transcript",
        )
    device_context = get_ha_context_by_label("voice-assistant")

    system_prompt = (
        "You are a smart home assistant.\n"
        f"Devices:\n{device_context}\n"
        f"Current Speaker: {speaker_id}\n"
        "Control devices or answer questions based on status. You must answer in german and keep the answers brief. "
        "You musn't include any entity ids in the response text. "
        "Address the user by their name if it is known."
    )
    if room:
        system_prompt += f"\nThe user is currently in room: {room}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": transcribed_text},
    ]

    response = llm_client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        tools=ha_tools_definitions,
        tool_choice="auto",
    )

    msg = response.choices[0].message
    final_text_response = ""
    client_actions = []

    if msg.tool_calls:
        for tool in msg.tool_calls:
            function_name = tool.function.name
            function_args = json.loads(tool.function.arguments)

            if function_name == "manage_volume":
                level = function_args.get("level", 50)
                client_actions.append(
                    Action(type="set_volume", payload={"level": level})
                )
                final_text_response = "Lautstärke wurde angepasst."

            elif function_name == "set_timer":
                seconds = function_args.get("duration_seconds", 0)
                client_actions.append(
                    Action(type="start_timer", payload={"duration_seconds": seconds})
                )
                minutes = seconds // 60
                final_text_response = f"Timer für {minutes} Minuten ist gestellt."

            else:
                final_text_response = execute_tool(
                    function_name, function_args, context=service_context
                )
    else:
        final_text_response = msg.content
    if not final_text_response:
        final_text_response = "Das habe ich nicht verstanden."

    try:
        audio_b64 = tts_client.generate_audio(final_text_response)
    except Exception as e:
        logger.error(f"TTS Error: {e}")
        audio_b64 = None

    return OrchestratorResponse(
        status="success",
        transcription=transcribed_text,
        response_text=final_text_response,
        audio_b64=audio_b64,
        actions=client_actions,
    )


def start():
    """Entry point for the packaged application"""
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    start()
