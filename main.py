from fastapi import FastAPI, HTTPException
from spotify_client import SpotifyClient
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
from typing import List, Optional
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

    # Validation checks (Optional)
    if settings.llm_auth_required and settings.llm_api_key.get_secret_value() == "nop":
        logger.warning("LLM Auth is enabled but API Key is default 'nop'.")

    yield
    # Shutdown Logic (if any)


logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Orchestrator")


app = FastAPI(lifespan=lifespan)
ha_client = HomeAssistantClient(
    base_url=settings.ha_url, token=settings.ha_token.get_secret_value()
)
spotify_client = SpotifyClient(
    settings.spotify_client_id,
    settings.spotify_client_secret,
    settings.spotify_redirect_url,
)

service_context = {"ha": ha_client, "spotify": spotify_client}

tts_client = TTSClient(tts_url=settings.tts_url)
# --- Initialize Clients using Settings ---

# OpenAI Client (Generic)
llm_client = OpenAI(
    base_url=settings.llm_url, api_key=settings.llm_api_key.get_secret_value()
)

# Headers for HA
# Note: get_secret_value() is needed to reveal the actual string from SecretStr
ha_headers = {
    "Authorization": f"Bearer {settings.ha_token.get_secret_value()}",
    "Content-Type": "application/json",
}


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


class VoiceCommand(BaseModel):
    text: str
    room: Optional[str] = None


# --- HA HELPER FUNCTIONS ---


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


@app.post("/process")
async def process_command(cmd: VoiceCommand):
    print(f"Processing: {cmd.text}")

    # 1. Get Context
    device_context = get_ha_context_by_label("voice-assistant")
    logger.info(device_context)

    system_prompt = (
        "You are a smart home assistant.\n"
        f"Devices:\n{device_context}\n"
        "Control devices or answer questions based on status. Answer in german."
    ) + (f"The user is currently in room {cmd.room}" if cmd.room else "")

    # 2. LLM Call
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": cmd.text},
    ]

    response = llm_client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        tools=ha_tools_definitions,  # Pass the loaded JSON here
        tool_choice="auto",
    )

    msg = response.choices[0].message
    final_text_response = ""
    # 3. Dynamic Execution
    if msg.tool_calls:
        for tool in msg.tool_calls:
            function_name = tool.function.name
            function_args = json.loads(tool.function.arguments)

            # ONE LINE to handle ANY tool:
            result_text = execute_tool(
                function_name, function_args, context=service_context
            )

            # (Optional) Append result to chat history if you want a multi-turn conversation
            # messages.append(msg)
            # messages.append(
            #     {"role": "tool", "tool_call_id": tool.id, "content": result_text}
            # )
    else:
        final_text_response = msg.content

    if not final_text_response:
        final_text_response = "Alles klärchen."

    # 4. Generate TTS
    audio_b64 = tts_client.generate_audio(final_text_response)

    # 2. Return JSON with both Text and Audio
    return JSONResponse(
        content={"response_text": final_text_response, "audio_b64": audio_b64}
    )


def start():
    """Entry point for the packaged application"""
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    start()
