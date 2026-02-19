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
        return f"Okay, {action.replace('_', ' ')} executed for {entity_id}."
    else:
        return f"I'm sorry, I failed to control {entity_id}."


def set_temperature(context: Any, **kwargs):
    """
    Tool: Set thermostat temperature.
    """
    temp = kwargs.get("temperature")
    success = context["ha"].call_service("climate", "set_temperature", kwargs)

    if success:
        return f"The thermostat is set to {temp} degrees."
    else:
        return "I couldn't set the temperature."


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


TOOL_MAPPING = {
    "control_light": control_light,
    "set_temperature": set_temperature,
    "play_music": play_spotify_music,  # Add to registry
}


def execute_tool(tool_name: str, tool_args: dict, context: Any = None) -> str:
    """
    Universal function to execute any tool found in the mapping.
    """
    if tool_name not in TOOL_MAPPING:
        return f"Error: Tool '{tool_name}' is defined in JSON but not implemented in Python."

    try:
        func = TOOL_MAPPING[tool_name]
        return func(context, **tool_args)
    except Exception as e:
        logger.error(f"Tool execution failed: {e}")
        return f"I tried to execute {tool_name}, but an error occurred."
