import logging
import datetime
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


def clear_queue(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")

    # Format the entity_id exactly like the other media_player functions
    entity_id = f"media_player.{sanitize_room(room)}"
    payload = {"entity_id": entity_id}

    try:
        # Standard media_player service to clear the playlist/queue
        context["ha"].call_service("media_player", "clear_playlist", payload)

        # Return a natural German confirmation for the LLM
        return f"Die Warteschlange im {room} wurde geleert."

    except Exception as e:
        return f"Fehler beim Leeren der Warteschlange: {e}"


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
    entity_id = f"media_player.{sanitize_room(room)}"
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
    entity_id = f"media_player.{sanitize_room(room)}"

    payload = {"entity_id": entity_id}

    try:
        # We use the standard media_player domain to pause/stop
        context["ha"].call_service("media_player", "media_pause", payload)

        # Return a context string so the LLM knows it succeeded
        return f"Musik im {room} wurde gestoppt."

    except Exception as e:
        return f"Fehler beim Stoppen der Musik: {e}"


def next_track(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"media_player.{sanitize_room(room)}"
    payload = {"entity_id": entity_id}

    try:
        # Standard media_player service to skip track
        context["ha"].call_service("media_player", "media_next_track", payload)
        return f"Nächstes Lied im {room} wird gespielt."
    except Exception as e:
        return f"Fehler beim Überspringen des Liedes: {e}"


def previous_track(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"media_player.{sanitize_room(room)}"
    payload = {"entity_id": entity_id}

    try:
        # Standard media_player service to go back
        context["ha"].call_service("media_player", "media_previous_track", payload)
        return f"Vorheriges Lied im {room} wird gespielt."
    except Exception as e:
        return f"Fehler beim Zurückspringen: {e}"


def queue_music(context, **kwargs):
    query = kwargs.get("query")
    media_type = kwargs.get("media_type", "track")
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"media_player.{sanitize_room(room)}"

    payload = {
        "entity_id": entity_id,
        "media_id": query,
        "media_type": media_type,
        "enqueue": "add",  # "add" appends to the queue, "next" plays right after current
    }
    try:
        context["ha"].call_service("music_assistant", "play_media", payload)
        return ""  # Empty return for natural confirmation handling
    except Exception as e:
        return f"Fehler beim Einreihen der Musik: {e}"


def resume_music(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"media_player.{sanitize_room(room)}"
    payload = {"entity_id": entity_id}

    try:
        # We use the standard media_player domain to play/resume
        context["ha"].call_service("media_player", "media_play", payload)
        return f"Musik im {room} wird fortgesetzt."
    except Exception as e:
        return f"Fehler beim Fortsetzen der Musik: {e}"


def sanitize_room(room):
    room = (
        room.lower()
        .replace(" ", "_")
        .replace("ü", "ue")
        .replace("ö", "oe")
        .replace("ä", "ae")
    )
    return room


def whats_playing(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"media_player.{room.lower().replace(' ', '_')}"

    state_data = context["ha"].get_state(entity_id)

    if not state_data:
        return f"Konnte den Player-Status im {room} nicht abrufen."

    state = state_data.get("state")
    attributes = state_data.get("attributes", {})

    if state in ["idle", "off", "standby", "unknown", "unavailable"]:
        return f"Im {room} wird gerade nichts abgespielt."

    title = attributes.get("media_title", "einem unbekannten Titel")
    artist = attributes.get("media_artist", "einem unbekannten Künstler")

    if state == "paused":
        return f"Die Musik ist im {room} pausiert. Das aktuelle Lied ist '{title}' von {artist}."
    else:
        return f"Im {room} läuft gerade '{title}' von {artist}."


def set_timer(context, **kwargs):
    duration_seconds = kwargs.get("duration_seconds")
    room = kwargs.get("room", "wohnzimmer")

    # Map the room to a specific HA timer entity (e.g., timer.wohnzimmer)
    entity_id = f"timer.{sanitize_room(room)}"

    # Convert seconds to a formatted string (HH:MM:SS) for HA compatibility
    duration_str = str(datetime.timedelta(seconds=duration_seconds))

    payload = {"entity_id": entity_id, "duration": duration_str}

    try:
        # Start the timer in Home Assistant
        context["ha"].call_service("timer", "start", payload)
        return f"Timer für {duration_seconds} Sekunden im {room} gestartet."
    except Exception as e:
        return f"Fehler beim Starten des Timers: {e}"


def cancel_timer(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"timer.{room.lower().replace(' ', '_')}"

    # Use your client's call_service and check the boolean return
    success = context["ha"].call_service("timer", "cancel", {"entity_id": entity_id})

    if success:
        return f"Timer im {room} wurde abgebrochen."
    else:
        return (
            f"Fehler: Konnte den Timer im {room} nicht abbrechen. Überprüfe die Logs."
        )


def timer_remaining(context, **kwargs):
    room = kwargs.get("room", "wohnzimmer")
    entity_id = f"timer.{room.lower().replace(' ', '_')}"

    # Use your client's built-in get_state method
    state_data = context["ha"].get_state(entity_id)

    if not state_data:
        return f"Konnte den Timer-Status im {room} nicht abrufen. Möglicherweise existiert er nicht."

    state = state_data.get("state")

    if state == "idle":
        return f"Es läuft aktuell kein Timer im {room}."

    elif state == "active":
        attributes = state_data.get("attributes", {})
        finishes_at_str = attributes.get("finishes_at")

        if finishes_at_str:
            # Parse HA's ISO string
            finishes_at = datetime.datetime.fromisoformat(
                finishes_at_str.replace("Z", "+00:00")
            )
            now = datetime.datetime.now(datetime.timezone.utc)

            remaining = finishes_at - now
            total_seconds = int(remaining.total_seconds())

            if total_seconds <= 0:
                return f"Der Timer im {room} ist gerade abgelaufen."

            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)

            if hours > 0:
                return f"Der Timer läuft noch {hours} Stunden, {minutes} Minuten und {seconds} Sekunden."
            elif minutes > 0:
                return f"Der Timer läuft noch {minutes} Minuten und {seconds} Sekunden."
            else:
                return f"Der Timer läuft noch {seconds} Sekunden."
        else:
            return f"Der Timer im {room} ist aktiv, aber ich kann die verbleibende Zeit nicht berechnen."

    elif state == "paused":
        return f"Der Timer im {room} ist derzeit pausiert."
    else:
        return f"Der Status des Timers im {room} ist: {state}."


TOOL_MAPPING = {
    "control_light": control_light,
    "set_temperature": set_temperature,
    "play_music": play_music,
    "activate_scene": activate_scene,
    "stop_music": stop_music,
    "next_track": next_track,
    "previous_track": previous_track,
    "queue_music": queue_music,
    "resume_music": resume_music,
    "whats_playing": whats_playing,
    "clear_queue": clear_queue,
    "set_timer": set_timer,
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
