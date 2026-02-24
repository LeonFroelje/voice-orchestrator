import os
import json
import asyncio
import logging
import requests
from typing import Dict, Any

import aiomqtt
from openai import OpenAI

from config import settings
from ha_client import HomeAssistantClient
from tool_handler import execute_tool
from semantic_router import S3SemanticRouter
from stt_sanitizer import NgramSanitizer

# --- Initialization ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_FILE = os.path.join(BASE_DIR, "tools.json")

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Orchestrator")


def load_tools():
    if not os.path.exists(TOOLS_FILE):
        raise FileNotFoundError(f"Could not find {TOOLS_FILE}")
    with open(TOOLS_FILE, "r") as f:
        return json.load(f)


ha_tools_definitions = load_tools()

ha_client = HomeAssistantClient(
    base_url=settings.ha_url, token=settings.ha_token.get_secret_value()
)
service_context = {"ha": ha_client}

llm_client = OpenAI(
    base_url=settings.llm_url, api_key=settings.llm_api_key.get_secret_value()
)
semantic_router = S3SemanticRouter()
sanitizer = NgramSanitizer(threshold=0.75)

ha_headers = {
    "Authorization": f"Bearer {settings.ha_token.get_secret_value()}",
    "Content-Type": "application/json",
}
ROUTE_TOOL_MAP = {
    "media": [
        "play_music",
        "stop_music",
        "next_track",
        "previous_track",
        "queue_music",
        "resume_music",
        "whats_playing",
        "clear_queue",
        "manage_volume",
    ],
    "timers": ["set_timer", "cancel_timer", "timer_remaining"],
    "home_control": ["control_light", "set_temperature", "activate_scene"],
}
# State Management
active_sessions: Dict[str, float] = {}
pending_intents: Dict[str, Dict[str, Any]] = {}


# --- Helper Functions ---
def get_ha_context_by_label(label="voice-assistant"):
    """Uses HA Template API to find entities with a specific label."""
    template = (
        "{% for entity in label_entities('"
        + label
        + "') %}{{ entity }}, {{ states(entity) }}, {{ state_attr(entity, 'friendly_name') }}|{% endfor %}"
    )
    try:
        url = f"{settings.ha_url}/api/template"
        response = requests.post(url, headers=ha_headers, json={"template": template})
        response.raise_for_status()

        raw_data = response.text.strip().split("|")
        context_lines = []
        for line in raw_data:
            if line.strip():
                parts = line.split(",")
                if len(parts) >= 3:
                    eid, state, name = (
                        parts[0].strip(),
                        parts[1].strip(),
                        parts[2].strip(),
                    )
                    context_lines.append(
                        f'{{"entity_id": "{eid}", "friendly_name": "{name}", "state": {state}}}'
                    )
        return "\n".join(context_lines)
    except Exception as e:
        logger.error(f"Error fetching HA context: {e}")
        return "No devices found."


# --- Event Handlers ---
def handle_wakeword(room: str):
    """Lowers volume of the media_player in that room."""
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"
    try:
        state = ha_client.get_state(entity_id)
        if state and state.get("state") == "playing":
            current_volume = state.get("attributes", {}).get("volume_level", 0.5)
            active_sessions[room] = current_volume
            duck_volume = max(0.1, current_volume * 0.5)
            ha_client.call_service(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": duck_volume},
            )
    except Exception as e:
        logger.error(f"Failed to duck volume for {room}: {e}")


def handle_finished(room: str):
    """Restores the original volume."""
    if room in active_sessions:
        original_volume = active_sessions.pop(room)
        entity_id = f"media_player.{room.lower().replace(' ', '_')}"
        try:
            ha_client.call_service(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": original_volume},
            )
        except Exception as e:
            logger.error(f"Failed to restore volume for {room}: {e}")


# --- Core LLM Logic ---
def run_llm_inference(room: str, text: str, speaker_id: str) -> tuple[str, list]:
    """Runs synchronous LLM and tool calls. Executed in a background thread."""
    logger.info(f"Processing command for {room} (Speaker: {speaker_id}): '{text}'")

    # --- 1. Semantic Routing & Tool Filtering ---
    route = semantic_router.get_route(text)
    active_tools = ha_tools_definitions

    if route:
        logger.info(f"Semantic route matched: '{route}'. Filtering tools...")
        allowed_tool_names = ROUTE_TOOL_MAP.get(route, [])
        # Filter the massive tools.json payload down to just what is needed
        active_tools = [
            tool
            for tool in ha_tools_definitions
            if tool["function"]["name"] in allowed_tool_names
        ]
    else:
        logger.info("No clear semantic route matched. Using all available tools.")

    # --- 2. Build Context ---
    # Fetch strictly filtered device context from the HA client
    device_context = ha_client.get_dynamic_context(text, room, route)
    system_prompt = (
        "You are a smart home assistant.\n"
        f"Devices:\n{device_context}\n"
        f"Current Speaker: {speaker_id}\n"
        "Control devices or answer questions based on status. You must answer in german and keep the answers brief. "
        "You musn't include any entity ids in the response text. "
        "Address the user by their name if it is known."
        f"\nThe user is currently in room: {room}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    # If a route was matched but mapped to no tools, active_tools will be empty.
    # The LLM API expects `tools` to be undefined/None if empty.
    tools_param = active_tools if active_tools else None
    tool_choice_param = "auto" if active_tools else "none"

    response = llm_client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        tools=tools_param,
        tool_choice=tool_choice_param,
    )

    msg = response.choices[0].message
    logger.debug(f"Message: {msg}")
    final_text_response = ""
    client_actions = []

    # --- 4. Tool Execution ---
    if msg.tool_calls:
        for tool in msg.tool_calls:
            function_name = tool.function.name
            function_args = json.loads(tool.function.arguments)
            logger.debug(
                f"Function name: \t {function_name}\n Function arguments: \t {function_args}"
            )

            final_text_response = execute_tool(
                function_name, function_args, context=service_context
            )
    else:
        final_text_response = msg.content

    if not final_text_response:
        final_text_response = "Das habe ich nicht verstanden."

    return final_text_response, client_actions


# --- Async Orchestration ---
async def process_intent_if_ready(client: aiomqtt.Client, room: str):
    """Checks if both STT and Speaker ID have arrived. If so, runs the LLM task."""
    intent_data = pending_intents.get(room)
    if not intent_data:
        return

    text = intent_data.get("text")
    logger.debug(f"Transcribed text: {text}")
    text = sanitizer.sanitize(text)
    logger.debug(f"Sanitized text: {text}")

    speaker_id = intent_data.get("speaker_id")

    # Only proceed if we have both pieces of data
    if text is None or speaker_id is None:
        logger.debug(f"Either text {text} or speaker-id {speaker_id} was empty")
        return

    # Pop the data so we don't process it twice
    pending_intents.pop(room)

    if not text.strip():
        logger.info(f"Empty transcript for {room}. Aborting.")
        # Trigger finished event to restore volume
        await client.publish(
            f"voice/finished/{room}", payload=json.dumps({"room": room})
        )
        return

    try:
        # Run the heavy, synchronous LLM and HTTP requests in a background thread
        response_text, actions = await asyncio.to_thread(
            run_llm_inference, room, text, speaker_id
        )

        # 1. Publish Satellite Actions (if any)
        if actions:
            action_payload = {"actions": actions}
            await client.publish(
                f"satellite/{room}/action", payload=json.dumps(action_payload)
            )
            # Give satellite a tiny bit of time to process the action before TTS arrives
            await asyncio.sleep(0.1)

        # 2. Publish TTS Task
        tts_payload = {"room": room, "text": response_text}
        await client.publish("voice/tts/generate", payload=json.dumps(tts_payload))

    except Exception as e:
        logger.error(f"Error executing intent for {room}: {e}")
        await client.publish(
            f"voice/finished/{room}", payload=json.dumps({"room": room})
        )


async def main_async():
    logger.info(
        f"Starting Orchestrator connected to {settings.mqtt_host}:{settings.mqtt_port}"
    )

    try:
        async with aiomqtt.Client(
            settings.mqtt_host, port=settings.mqtt_port
        ) as client:
            await client.subscribe("voice/wakeword/+")
            await client.subscribe("voice/finished/+")
            await client.subscribe("voice/asr/text")
            await client.subscribe("voice/speaker/identified")

            logger.info("Listening for events...")

            async for message in client.messages:
                topic = message.topic.value
                payload = json.loads(message.payload.decode())
                room = payload.get("room")
                logger.debug(topic, payload, room)

                if not room:
                    continue

                if topic.startswith("voice/wakeword/"):
                    # Reset the pending state for this room cleanly
                    pending_intents[room] = {"text": None, "speaker_id": None}
                    await asyncio.to_thread(handle_wakeword, room)

                elif topic.startswith("voice/finished/"):
                    await asyncio.to_thread(handle_finished, room)

                elif topic == "voice/asr/text":
                    logger.info(f"Received STT for {room}")
                    # Ensure room dict exists (in case STT arrived before wakeword somehow)
                    pending_intents.setdefault(room, {})["text"] = payload.get(
                        "text", ""
                    )
                    # Fire-and-forget task to check and run
                    asyncio.create_task(process_intent_if_ready(client, room))

                elif topic == "voice/speaker/identified":
                    logger.debug(f"Received Speaker ID for {room}")
                    pending_intents.setdefault(room, {})["speaker_id"] = payload.get(
                        "speaker_id", "Unbekannt"
                    )
                    # Fire-and-forget task to check and run
                    asyncio.create_task(process_intent_if_ready(client, room))

    except aiomqtt.MqttError as error:
        logger.error(f"MQTT Error: {error}")
    except KeyboardInterrupt:
        logger.info("Shutting down Orchestrator...")


def main():
    """Synchronous wrapper for the setuptools entry point."""
    import asyncio

    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass
