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
semantic_cache = S3SemanticCache()
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
}
# State Management
active_sessions: Dict[str, float] = {}
pending_intents: Dict[str, Dict[str, Any]] = {}

FAST_PATH_MAP = {
    "musik stoppen": ("stop_music", {}),
    "musik fortsetzen": ("resume_music", {}),
    "nächstes lied bitte": ("next_track", {}),
    "leere die warteschlange": ("clear_queue", {}),
    "was läuft gerade": ("whats_playing", {}),
    "timer abbrechen": ("cancel_timer", {}),
    "timer stop": ("cancel_timer", {}),
    "wie viel zeit ist noch auf dem timer": ("timer_remaining", {}),
}


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
async def run_llm_inference(
    room: str, text: str, speaker_id: str, route: Optional[str]
) -> tuple[str, list]:
    """Runs synchronous LLM and tool calls. Executed in a background thread."""
    logger.info(f"Processing command for {room} (Speaker: {speaker_id}): '{text}'")

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
    device_context = await ha_client.get_dynamic_context(text, room, route)
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

    executed_tools = []
    # --- 4. Tool Execution ---
    if msg.tool_calls:
        for tool in msg.tool_calls:
            function_name = tool.function.name
            function_args = json.loads(tool.function.arguments)
            logger.debug(
                f"Function name: \t {function_name}\n Function arguments: \t {function_args}"
            )

            final_text_response = await execute_tool(
                function_name, function_args, context=service_context
            )
            executed_tools.append(tool)
    else:
        final_text_response = msg.content

    if not final_text_response:
        final_text_response = "Das habe ich nicht verstanden."

    return final_text_response, client_actions, executed_tools


async def resolve_and_execute_intent(
    room: str, text: str, speaker_id: str
) -> tuple[str, list]:
    """Handles the core AI routing logic: Cache -> Fast Path -> LLM -> Cache Learning."""
    actions = []

    # 1. Check the Semantic Tool Cache FIRST
    cached_tool, cached_args, cache_score = semantic_cache.get_cached_tool(
        text, threshold=0.92
    )

    if cached_tool:
        logger.info(
            f"⚡ CACHE HIT: '{text}' matched with score {cache_score:.2f}. Bypassing LLM."
        )
        tool_args = cached_args.copy()
        tool_args["room"] = room
        response_text = await execute_tool(
            cached_tool, tool_args, context=service_context
        )
        return response_text, actions

    # 2. Ask the Semantic Router for details (since cache missed)
    route, matched_text, score = semantic_router.get_match_details(text)

    # 3. Check for the Fast Path!
    if score >= 0.85 and matched_text in FAST_PATH_MAP:
        tool_name, static_args = FAST_PATH_MAP[matched_text]
        logger.info(
            f"⚡ FAST PATH TRIGGERED: '{text}' matched '{matched_text}' ({score:.2f}). Bypassing LLM."
        )

        tool_args = static_args.copy()
        tool_args["room"] = room
        response_text = await execute_tool(
            tool_name, tool_args, context=service_context
        )
        return response_text, actions

    # 4. Fallback: Run the full LLM Inference pipeline
    logger.info(f"Standard routing (Score: {score:.2f}). Delegating to LLM...")
    response_text, actions, executed_tools = await run_llm_inference(
        room, text, speaker_id, route
    )

    # 5. Learn the new phrase!
    if executed_tools:
        for tool in executed_tools:
            function_name = tool.function.name
            function_args = json.loads(tool.function.arguments)
            function_args.pop("room", None)  # Generalize for all rooms
            semantic_cache.add_to_cache(text, function_name, function_args)

    return response_text, actions


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

    # Only proceed if we have both pieces of data
    if text is None or speaker_id is None:
        logger.debug(f"Either text '{text}' or speaker-id '{speaker_id}' was empty")
        return

    # Pop the data so we don't process it twice
    pending_intents.pop(room)

    # Empty string check
    if not text.strip():
        logger.info(f"Empty transcript for {room}. Aborting.")
        await client.publish(
            f"voice/finished/{room}", payload=json.dumps({"room": room})
        )
        return

    logger.debug(f"Transcribed text: {text}")
    text = sanitizer.sanitize(text)
    logger.debug(f"Sanitized text: {text}")

    try:
        # Step 1: Figure out what to do
        response_text, actions = await resolve_and_execute_intent(
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
    logger.debug(ha_vocabulary_split)
    base_vocabulary = ["spiele musik", "musik stoppen", "timer", "lautstärke"]
    sanitizer.update_vocabulary(
        ha_vocabulary_split + ha_vocabulary_raw + base_vocabulary
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
                # logger.debug(topic, payload, room)

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
