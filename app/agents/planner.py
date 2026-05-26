import json
from app.core.state import AgentState
from app.utils.prompts import PLANNER_SYSTEM, PLANNER_USER
from app.utils.logger import logger

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import settings

VALID_INTENTS = {"billing", "compliance", "pharmacy", "patient", "dispatch"}


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


async def planner_node(state: AgentState) -> AgentState:
    llm = _build_llm()
    messages = [
        SystemMessage(content=PLANNER_SYSTEM),
        HumanMessage(content=PLANNER_USER.format(query=state.query)),
    ]

    try:
        response = await llm.ainvoke(messages)
        payload = json.loads(response.content)
        intent: str = payload.get("intent", "").lower().strip()
        confidence: float = float(payload.get("confidence", 0.0))

        if intent not in VALID_INTENTS:
            logger.warning(f"Planner returned unknown intent '{intent}'. Defaulting to 'billing'.")
            intent = "billing"
            confidence = 0.0

        logger.info(f"Planner → intent='{intent}' confidence={confidence:.2f}")
        state.intent = intent
        state.intent_confidence = confidence

    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(f"Planner failed to parse LLM response: {exc}")
        state.intent = "billing"
        state.intent_confidence = 0.0
        state.error = f"Planner classification error: {exc}"

    return state


def route_intent(state: AgentState) -> str:
    return state.intent
