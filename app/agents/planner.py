import json
from app.core.state import AgentState
from app.utils.prompts import PLANNER_SYSTEM, PLANNER_USER
from app.utils.logger import logger
from app.core.rbac import RBACManager, Role
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from app.config import settings

VALID_INTENTS = {"billing", "compliance", "pharmacy", "patient", "dispatch", "general"}


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


async def planner_node(state: AgentState) -> AgentState:
    llm = _build_llm()
    try:
        user_role = Role(state.role.lower())
    except ValueError:
        user_role = Role.GUEST
        
    role_context = RBACManager.get_role_context(user_role)
    system_prompt = f"{PLANNER_SYSTEM}\n\nUSER CONTEXT:\n{role_context}"
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=PLANNER_USER.format(query=state.query)),
    ]

    try:
        response = await llm.ainvoke(messages)
        payload = json.loads(response.content)
        intent: str = payload.get("intent", "").lower().strip()
        confidence: float = float(payload.get("confidence", 0.0))

        if intent not in VALID_INTENTS:
            logger.warning(f"Planner returned unknown intent '{intent}'. Defaulting to 'general'.")
            intent = "general"
            confidence = 0.0

        logger.info(f"Planner → intent='{intent}' confidence={confidence:.2f}")
        
        # Check RBAC
        if intent != "general" and not RBACManager.can_access_domain(user_role, intent):
            logger.warning(f"RBAC block: Role '{user_role.value}' denied access to '{intent}'.")
            state.intent = "unauthorized"
            state.intent_confidence = 1.0
            state.error = f"Unauthorized. Your role '{user_role.value}' cannot access the '{intent}' domain."
            return state

        state.intent = intent
        state.intent_confidence = confidence

    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(f"Planner failed to parse LLM response: {exc}")
        state.intent = "general"
        state.intent_confidence = 0.0
        state.error = f"Planner classification error: {exc}"

    return state


def route_intent(state: AgentState) -> str:
    return state.intent
