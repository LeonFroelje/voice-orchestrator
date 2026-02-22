import requests
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches the complete state object (state and attributes) for a given entity ID.
        """
        url = f"{self.base_url}/api/states/{entity_id}"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=5)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.warning(f"Entity '{entity_id}' not found in Home Assistant.")
                return None
            else:
                logger.error(
                    f"Failed to fetch state for {entity_id}. Status: {response.status_code}, Response: {response.text}"
                )
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error while fetching state for {entity_id}: {e}")
            return None

    def call_service(
        self, domain: str, service: str, service_data: Dict[str, Any]
    ) -> bool:
        """
        Calls a Home Assistant service (e.g., light.turn_on).
        """
        url = f"{self.base_url}/api/services/{domain}/{service}"
        try:
            logger.info(f"Calling HA: {domain}.{service} with {service_data}")
            response = requests.post(url, headers=self.headers, json=service_data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Failed to call service {domain}.{service}: {e}")
            return False

    def get_states_context(self, label: str = "voice-assistant") -> str:
        """
        Fetches entities with a specific label using the HA Template API.
        Returns a formatted string ready for the LLM system prompt.
        """
        # Jinja2 template to format the output exactly how we want it
        template = (
            "{% for entity in label_entities('" + label + "') %}"
            "{{ state_attr(entity, 'friendly_name') }} ({{ entity }}) is {{ states(entity) }}"
            "{% if is_state_attr(entity, 'temperature', '!=', None) %}"
            " set to {{ state_attr(entity, 'temperature') }}{% endif %}|"
            "{% endfor %}"
        )

        try:
            url = f"{self.base_url}/api/template"
            response = requests.post(
                url, headers=self.headers, json={"template": template}
            )
            response.raise_for_status()

            # The template returns a pipe-separated string; we split it into lines
            raw_text = response.text.strip()
            if not raw_text:
                return "No devices found."

            # formatting into a list
            lines = [
                f"- {line.strip()}" for line in raw_text.split("|") if line.strip()
            ]
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error fetching HA context: {e}")
            return "Error: Could not fetch device states."
