import logging
import requests
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Map semantic routes to Home Assistant domains
ROUTE_DOMAIN_MAP = {
    "media": ["media_player"],
    "timers": ["timer"],
    "home_control": ["light", "climate", "switch", "scene", "cover"],
}


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # Cache for Home Assistant Area names
        self.areas: List[str] = []

    def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Fetches the current state and attributes of a specific entity."""
        url = f"{self.base_url}/api/states/{entity_id}"
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching state for {entity_id}: {e}")
            return None

    def call_service(self, domain: str, service: str, payload: Dict[str, Any]) -> bool:
        """Calls a Home Assistant service."""
        url = f"{self.base_url}/api/services/{domain}/{service}"
        try:
            logger.debug(f"Homeassistant json payload: {payload}")
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling service {domain}.{service}: {e}")
            return False

    def _load_areas(self):
        """Fetches all human-readable area names from Home Assistant and caches them."""
        try:
            url = f"{self.base_url}/api/template"
            # HA template to get all area names separated by a pipe character
            template = "{% for area in areas() %}{{ area_name(area) }}|{% endfor %}"
            response = requests.post(
                url, headers=self.headers, json={"template": template}
            )
            response.raise_for_status()

            # Clean up the response into a list of lowercase area names
            self.areas = [
                a.strip().lower()
                for a in response.text.split("|")
                if a.strip() and a.strip() != "None"
            ]
            logger.info(f"Loaded HA areas for dynamic routing: {self.areas}")
        except Exception as e:
            logger.error(f"Failed to load areas from HA: {e}")
            # Fallback list just in case the API call fails
            self.areas = ["wohnzimmer", "küche", "schlafzimmer", "bad", "flur", "büro"]

    def get_dynamic_context(
        self, text: str, room: str, route: str, label: str = "voice-assistant"
    ) -> str:
        """
        Fetches entities dynamically filtered by route domain and room context.
        Uses Home Assistant's template engine to minimize data transfer.
        """
        # 1. Map route to allowed domains
        allowed_domains = ROUTE_DOMAIN_MAP.get(route, [])
        if not allowed_domains:
            # Fallback: if router failed, allow standard controllable domains
            allowed_domains = [
                "light",
                "climate",
                "switch",
                "scene",
                "media_player",
                "timer",
            ]

        # 2. Check if the user is asking about a different room
        if not self.areas:
            self._load_areas()

        text_lower = text.lower()
        # Find if any known area (other than the current room) is mentioned in the text
        mentioned_other_rooms = [
            a for a in self.areas if a in text_lower and a != room.lower()
        ]
        # It's a strictly local command if no other rooms were mentioned
        is_local_command = len(mentioned_other_rooms) == 0

        # 3. Format variables for the Jinja template
        domains_str = str(allowed_domains).replace("'", '"')
        is_local_str = str(is_local_command).lower()  # Python 'True' to Jinja 'true'

        # 4. Build the Template
        # We use quadruple braces {{{{ }}}} to escape Python's f-string formatting so Jinja's {{ }} survive
        template = f"""
        {{% set allowed_domains = {domains_str} %}}
        {{% set current_room = '{room.lower()}' %}}
        {{% set is_local = {is_local_str} %}}
        
        {{% for entity in label_entities('{label}') %}}
          {{% set domain = entity.split('.')[0] %}}
          {{% if domain in allowed_domains %}}
            {{% set entity_area = area_name(entity) | lower %}}
            
            {{# Include if global command, OR if it matches the current room, OR if it has no room assigned #}}
            {{% if not is_local or current_room == entity_area or entity_area == 'none' %}}
              {{{{ entity }}}},{{{{ states(entity) }}}},{{{{ state_attr(entity, 'friendly_name') }}}}|
            {{% endif %}}
            
          {{% endif %}}
        {{% endfor %}}
        """

        try:
            url = f"{self.base_url}/api/template"
            response = requests.post(
                url, headers=self.headers, json={"template": template}
            )
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
                        # Format into concise JSON-like strings for the LLM prompt
                        context_lines.append(
                            f'{{"entity_id": "{eid}", "name": "{name}", "state": "{state}"}}'
                        )

            final_context = "\n".join(context_lines)
            return (
                final_context
                if final_context
                else "No relevant devices found for this command."
            )

        except Exception as e:
            logger.error(f"Error fetching dynamic HA context: {e}")
            return "No devices found."
