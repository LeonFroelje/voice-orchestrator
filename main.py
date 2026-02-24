import os
import json
import asyncio
import logging
import requests
from typing import Dict, Any, Optional

import aiomqtt
from openai import OpenAI

from config import settings
from ha_client import HomeAssistantClient
from tool_handler import execute_tool
from semantic_router import S3SemanticRouter
from semantic_cache import S3SemanticCache
from sanitizer import NgramSanitizer
from intent_processor import IntentProcessor

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
        raw_tools = json.load(f)

    clean_tools = []
    exact_tools_registry = set()

    for tool in raw_tools:
        tool_copy = tool.copy()

        if tool_copy.pop("exact_cache_only", False):
            exact_tools_registry.add(tool_copy["function"]["name"])

        clean_tools.append(tool_copy)

    return clean_tools, exact_tools_registry


ha_tools_definitions, exact_tools_registry = load_tools()

ha_client = HomeAssistantClient(
    base_url=settings.ha_url, token=settings.ha_token.get_secret_value()
)
service_context = {"ha": ha_client}

llm_client = OpenAI(
    base_url=settings.llm_url, api_key=settings.llm_api_key.get_secret_value()
)
semantic_router = S3SemanticRouter()
semantic_cache = S3SemanticCache(exact_tools=exact_tools_registry)
sanitizer = NgramSanitizer(threshold=settings.dice_coefficient)

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
    "information": ["get_current_time", "get_weather"],
}

intent_processor = IntentProcessor(
    ha_client=ha_client,
    llm_client=llm_client,
    semantic_router=semantic_router,
    semantic_cache=semantic_cache,
    tools_definitions=ha_tools_definitions,
    route_map=ROUTE_TOOL_MAP,
)
# State Management
active_sessions: Dict[str, float] = {}
pending_intents: Dict[str, Dict[str, Any]] = {}


# --- Event Handlers ---
async def handle_wakeword(room: str):
    """Lowers volume of the media_player in that room."""
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"
    try:
        state = await ha_client.get_state(entity_id)
        if state and state.get("state") == "playing":
            current_volume = state.get("attributes", {}).get("volume_level", 0.5)
            active_sessions[room] = current_volume
            duck_volume = max(0.1, current_volume * 0.5)
            await ha_client.call_service(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": duck_volume},
            )
    except Exception as e:
        logger.error(f"Failed to duck volume for {room}: {e}")


async def handle_finished(room: str):
    """Restores the original volume."""
    if room in active_sessions:
        original_volume = active_sessions.pop(room)
        entity_id = f"media_player.{room.lower().replace(' ', '_')}"
        try:
            await ha_client.call_service(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": original_volume},
            )
        except Exception as e:
            logger.error(f"Failed to restore volume for {room}: {e}")


async def publish_response(
    client: aiomqtt.Client, room: str, response_text: str, actions: list
):
    """Handles MQTT publishing for satellite hardware actions and TTS generation."""
    if actions:
        action_payload = {"actions": actions}
        await client.publish(
            f"satellite/{room}/action", payload=json.dumps(action_payload)
        )
        # Give satellite a tiny bit of time to process the action before TTS arrives
        await asyncio.sleep(0.1)

    tts_payload = {"room": room, "text": response_text}
    await client.publish("voice/tts/generate", payload=json.dumps(tts_payload))


async def process_intent_if_ready(client: aiomqtt.Client, room: str):
    """Orchestrator entrypoint. Checks state, sanitizes input, and runs the pipeline."""
    intent_data = pending_intents.get(room)
    if not intent_data:
        return

    text = intent_data.get("text")
    speaker_id = intent_data.get("speaker_id")

    if text is None or speaker_id is None:
        return

    pending_intents.pop(room)

    if not text.strip():
        logger.info(f"Empty transcript for {room}. Aborting.")
        await client.publish(
            f"voice/finished/{room}", payload=json.dumps({"room": room})
        )
        return

    text = sanitizer.sanitize(text)

    try:
        # Step 1: Figure out what to do using the extracted class!
        response_text, actions = await intent_processor.resolve_and_execute_intent(
            room, text, speaker_id
        )

        # Step 2: Send the commands back to the house
        await publish_response(client, room, response_text, actions)

    except Exception as e:
        logger.error(f"Error executing intent for {room}: {e}")
        await client.publish(
            f"voice/finished/{room}", payload=json.dumps({"room": room})
        )


async def main_async():
    logger.info(
        f"Starting Orchestrator connected to {settings.mqtt_host}:{settings.mqtt_port}"
    )
    ha_vocabulary_raw = await ha_client.get_voice_vocabulary()
    ha_vocabulary_split = []
    for vocab in ha_vocabulary_raw:
        if " " in vocab:
            ha_vocabulary_split += vocab.split(" ")
    # Combine with any hardcoded base vocabulary (like system commands)
    base_vocabulary = [
        "spiele musik",
        "musik stoppen",
        "timer",
        "lautstärke",
        "musik aus",
    ]
    sanitizer.update_vocabulary(
        ha_vocabulary_split + ha_vocabulary_raw + base_vocabulary
    )
    logger.info(f"Sanitizer vocabulary: {sanitizer.known_vocabulary}")

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

                if not room:
                    continue

                if topic.startswith("voice/wakeword/"):
                    # Reset the pending state for this room cleanly
                    pending_intents[room] = {"text": None, "speaker_id": None}
                    await asyncio.create_task(handle_wakeword(room))

                elif topic.startswith("voice/finished/"):
                    await asyncio.create_task(handle_finished(room))

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
