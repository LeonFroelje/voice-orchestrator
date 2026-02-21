import logging
import json
from typing import Dict, Any, Callable
# Import your HA service call function here or pass it in
# For this example, I'll assume it's imported from a common util or passed as a dependency

logger = logging.getLogger(__name__)

# --- Actual Python Functions ---


def control_light(context: Any, **kwargs):
    """
    Tool: Turn a light on or off.
    """
    action = kwargs.pop("action")  # 'turn_on' or 'turn_off'
    entity_id = kwargs.get("entity_id")

    # Use the client object passed in as 'context'
    success = context["ha"].call_service("homeassistant", action, kwargs)

    if success:
        return f"Okay, Miau miau."
    else:
        return "Tut mir leid, ich konnte die Aktion leider nicht ausführen."


def set_temperature(context: Any, **kwargs):
    """
    Tool: Set thermostat temperature.
    """
    temp = kwargs.get("temperature")
    success = context["ha"].call_service("climate", "set_temperature", kwargs)

    if success:
        return f"Temperatur auf {temp} Grad gesetzt."
    else:
        return f"Konnte Temperatur nicht auf {temp} grad setzen."


def play_spotify_music(context: dict, **kwargs):
    """
    Tool: Search and play music on a specific device.
    """
    spotify = context["spotify"]
    device = kwargs.get("device_name")
    query = kwargs.get("query")
    category = kwargs.get("category", "track")  # default to track

    success, message = spotify.search_and_play(
        device_name=device, query=query, search_type=category
    )

    return message


def activate_scene(context: Any, **kwargs):
    """
    Tool: Activate a Home Assistant scene.
    """
    entity_id = kwargs.get("entity_id")

    if not entity_id:
        return "Error: No entity_id provided for the scene."

    # In Home Assistant, you turn on a scene to activate it
    success = context["ha"].call_service("scene", "turn_on", {"entity_id": entity_id})

    if success:
        return f"Okay."
    else:
        return "Tut mir leid, konnte Szene nicht aktivieren"


def play_music(context, **kwargs):
    query = kwargs.get("query")
    media_type = kwargs.get("media_type", "track")
    room = kwargs.get("room")
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"
    payload = {
        "entity_id": entity_id,
        "media_id": query,
        "media_type": media_type,
        "enqueue": "play",  # Options: play, replace, next, add
    }
    try:
        # Use your HA client to call the service.
        # Domain is "music_assistant", Service is "play_media"
        context["ha"].call_service("music_assistant", "play_media", payload)

        # Return a natural confirmation for the LLM to process
        return ""
    except Exception as e:
        return f"Fehler beim Starten der Musik: {e}"


def stop_music(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")

    # Format the entity_id exactly like we did for play_music
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"

    payload = {"entity_id": entity_id}

    try:
        # We use the standard media_player domain to pause/stop
        context["ha"].call_service("media_player", "media_pause", payload)

        # Return a context string so the LLM knows it succeeded
        return f"Musik im {room} wurde gestoppt."

    except Exception as e:
        return f"Fehler beim Stoppen der Musik: {e}"


# Update your mapping to include the new tool
TOOL_MAPPING = {
    "control_light": control_light,
    "set_temperature": set_temperature,
    "play_music": play_music,
    "activate_scene": activate_scene,
    "stop_music": stop_music,
}


def execute_tool(tool_name, tool_args, context):
    ha_client = context.get("ha")
    if not tool_name in TOOL_MAPPING:
        return f"Error: Tool '{tool_name}' is defined in JSON but not implemented in Python."

    try:
        func = TOOL_MAPPING[tool_name]
        return func(context, **tool_args)
    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        # return f"I tried to execute {tool_name}, but an error occurred."
