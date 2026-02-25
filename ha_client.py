import logging
import aiohttp
from typing import Dict, Any, Optional, List

logger = logging.getLogger("HaClient")

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
        self.areas: List[str] = []

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/api/states/{entity_id}"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching state for {entity_id}: {e}")
            return None

    async def call_service(
        self, domain: str, service: str, payload: Dict[str, Any]
    ) -> bool:
        url = f"{self.base_url}/api/services/{domain}/{service}"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json=payload) as response:
                    response.raise_for_status()
                    return True
        except aiohttp.ClientError as e:
            logger.error(f"Error calling service {domain}.{service}: {e}")
            return False

    async def _load_areas(self):
        url = f"{self.base_url}/api/template"
        template = "{% for area in areas() %}{{ area_name(area) }}|{% endfor %}"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json={"template": template}) as response:
                    response.raise_for_status()
                    text = await response.text()

            self.areas = [
                a.strip().lower()
                for a in text.split("|")
                if a.strip() and a.strip() != "None"
            ]
            logger.info(f"Loaded HA areas: {self.areas}")
        except Exception as e:
            logger.error(f"Failed to load areas from HA: {e}")
            self.areas = ["wohnzimmer", "küche", "schlafzimmer", "bad"]

    async def get_voice_vocabulary(self, label: str = "voice-assistant") -> list[str]:
        template = f"""
        {{% for area in areas() %}}{{{{ area_name(area) }}}}|{{% endfor %}}
        {{% for entity in label_entities('{label}') %}}{{{{ state_attr(entity, 'friendly_name') }}}}|{{% endfor %}}
        """
        url = f"{self.base_url}/api/template"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json={"template": template}) as response:
                    response.raise_for_status()
                    text = await response.text()

            raw_data = text.strip().split("|")
            return [
                item.strip().lower()
                for item in raw_data
                if item.strip() and item.strip().lower() != "none"
            ]
        except Exception as e:
            logger.error(f"Failed to load vocabulary from HA: {e}")
            return []

    async def get_dynamic_context(
        self, text: str, room: str, route: str, label: str = "voice-assistant"
    ) -> str:
        allowed_domains = ROUTE_DOMAIN_MAP.get(
            route, ["light", "climate", "switch", "scene", "media_player", "timer"]
        )

        if not self.areas:
            await self._load_areas()

        text_lower = text.lower()
        mentioned_other_rooms = [
            a for a in self.areas if a in text_lower and a != room.lower()
        ]
        is_local_command = False

        domains_str = str(allowed_domains).replace("'", '"')
        is_local_str = str(is_local_command).lower()

        template = f"""
        {{% set allowed_domains = {domains_str} %}}
        {{% set current_room = '{room.lower()}' %}}
        {{% set is_local = {is_local_str} %}}
        {{% for entity in label_entities('{label}') %}}
          {{% set domain = entity.split('.')[0] %}}
          {{% if domain in allowed_domains %}}
            {{% set entity_area = area_name(entity) | lower %}}
            {{% if not is_local or current_room == entity_area or entity_area == 'none' %}}
              {{{{ entity }}}},{{{{ states(entity) }}}},{{{{ state_attr(entity, 'friendly_name') }}}}|
            {{% endif %}}
          {{% endif %}}
        {{% endfor %}}
        """
        url = f"{self.base_url}/api/template"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.post(url, json={"template": template}) as response:
                    response.raise_for_status()
                    text = await response.text()

            raw_data = text.strip().split("|")
            context_lines = [
                f'{{"entity_id": "{p[0].strip()}", "name": "{p[2].strip()}", "state": "{p[1].strip()}"}}'
                for line in raw_data
                if line.strip() and len(p := line.split(",")) >= 3
            ]
            final_context = "\n".join(context_lines)
            return final_context if final_context else "No relevant devices found."
        except Exception as e:
            logger.error(f"Error fetching dynamic HA context: {e}")
            return "No devices found."
