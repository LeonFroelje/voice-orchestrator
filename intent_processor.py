import json
import logging
from typing import Optional

from tool_handler import execute_tool
from config import settings

logger = logging.getLogger("IntentProcessor")


class IntentProcessor:
    def __init__(
        self,
        ha_client,
        llm_client,
        semantic_router,
        semantic_cache,
        tools_definitions: list,
        route_map: dict,
    ):
        self.ha_client = ha_client
        self.llm_client = llm_client
        self.semantic_router = semantic_router
        self.semantic_cache = semantic_cache
        self.tools_definitions = tools_definitions
        self.route_map = route_map
        self.service_context = {"ha": ha_client}

    async def run_llm_inference(
        self, room: str, text: str, speaker_id: str, route: Optional[str]
    ) -> tuple[str, list, list]:
        logger.info(f"Processing command for {room} (Speaker: {speaker_id}): '{text}'")

        active_tools = self.tools_definitions

        if route:
            logger.info(f"Semantic route matched: '{route}'. Filtering tools...")
            allowed_tool_names = self.route_map.get(route, [])
            active_tools = [
                tool
                for tool in self.tools_definitions
                if tool["function"]["name"] in allowed_tool_names
            ]
        else:
            logger.info("No clear semantic route matched. Using all available tools.")

        device_context = await self.ha_client.get_dynamic_context(text, room, route)
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

        tools_param = active_tools if active_tools else None
        tool_choice_param = "auto" if active_tools else "none"

        response = self.llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=tools_param,
            tool_choice=tool_choice_param,
        )

        msg = response.choices[0].message
        final_text_response = ""
        client_actions = []
        executed_tools = []

        if msg.tool_calls:
            for tool in msg.tool_calls:
                function_name = tool.function.name
                function_args = json.loads(tool.function.arguments)
                final_text_response = await execute_tool(
                    function_name, function_args, context=self.service_context
                )
                executed_tools.append(tool)
        else:
            final_text_response = msg.content

        if not final_text_response:
            final_text_response = "Das habe ich nicht verstanden."

        return final_text_response, client_actions, executed_tools

    async def resolve_and_execute_intent(
        self, room: str, text: str, speaker_id: str
    ) -> tuple[str, list]:
        actions = []

        # 1. Check the Semantic Tool Cache
        cached_tool, cached_args, cache_score = self.semantic_cache.get_cached_tool(
            text, threshold=0.92
        )

        if cached_tool:
            logger.info(
                f"CACHE HIT: '{text}' matched with score {cache_score:.2f}. Bypassing LLM."
            )
            tool_args = cached_args.copy()
            tool_args["room"] = room
            response_text = await execute_tool(
                cached_tool, tool_args, context=self.service_context
            )
            return response_text, actions

        # 2. Semantic Routing & LLM Fallback
        route, matched_text, score = self.semantic_router.get_match_details(text)
        logger.info(f"Standard routing (Score: {score:.2f}). Delegating to LLM...")

        if score <= 0.6:
            route = None
        response_text, actions, executed_tools = await self.run_llm_inference(
            room, text, speaker_id, route
        )

        # 3. Cache Learning
        if executed_tools:
            for tool in executed_tools:
                function_name = tool.function.name
                function_args = json.loads(tool.function.arguments)
                function_args.pop("room", None)
                self.semantic_cache.add_to_cache(text, function_name, function_args)

        return response_text, actions
