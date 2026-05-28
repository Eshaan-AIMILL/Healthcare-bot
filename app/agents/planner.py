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
    # --- Jailbreak & Prompt Injection Heuristic ---
    query_lower = state.query.lower()
    jailbreak_phrases = [
        "ignore previous", "ignore your previous", "admin mode", "forget instructions",
        "system prompt", "bypass", "override"
    ]
    if any(phrase in query_lower for phrase in jailbreak_phrases):
        logger.warning(f"Jailbreak attempt detected from user ID: {getattr(state.security_context, 'user_id', 'unknown')}")
        state.intent = "unauthorized"
        state.intent_confidence = 1.0
        state.error = "Unauthorized. Security Violation: Prompt injection attempt detected and blocked."
        return state

    llm = _build_llm()

    # Extract robust SecurityContext
    ctx = state.security_context
    if not ctx:
        logger.error("Planner missing SecurityContext! Defaulting to Guest.")
        user_role = Role.GUEST
    else:
        try:
            user_role = Role(ctx.enterprise_role)
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

        # Support both old single-intent {"intent": "..."} and new multi-intent {"intents": [...]}
        raw_intents = payload.get("intents") or [payload.get("intent", "")]
        is_cross_domain = payload.get("is_cross_domain", False)
        confidence: float = float(payload.get("confidence", 0.0))

        # Sanitize: keep only valid known intents
        valid_detected = [i.lower().strip() for i in raw_intents if i.lower().strip() in VALID_INTENTS]

        if not valid_detected:
            logger.warning(f"Planner returned no valid intents. Defaulting to 'general'.")
            valid_detected = ["general"]
            is_cross_domain = False
            confidence = 0.0

        logger.info(f"Planner -> intents={valid_detected} cross_domain={is_cross_domain} confidence={confidence:.2f}")

        # RBAC check: filter out domains the role cannot access
        denied = []
        permitted = []
        for intent in valid_detected:
            if intent == "general":
                permitted.append(intent)
            elif RBACManager.can_access_domain(user_role, intent):
                permitted.append(intent)
            else:
                denied.append(intent)

        if denied:
            logger.warning(f"RBAC block: Role '{user_role.value}' denied access to {denied}.")

        if not permitted:
            # All requested domains were denied
            state.intent = "unauthorized"
            state.intent_confidence = 1.0
            state.error = f"Unauthorized. Your role '{user_role.value}' cannot access the requested domain(s): {denied}."
            return state

        if denied and permitted:
            # Partial access: warn but continue with permitted domains
            if permitted != ["general"]:
                state.error = f"Note: Access to {denied} was denied for your role. Showing results for {permitted} only."

        # Route accordingly
        if len(permitted) > 1 and is_cross_domain:
            state.intent = "cross_domain"
            state.intents = permitted
        else:
            state.intent = permitted[0]
            state.intents = permitted

        state.intent_confidence = confidence

    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(f"Planner failed to parse LLM response: {exc}")
        state.intent = "general"
        state.intents = ["general"]
        state.intent_confidence = 0.0
        state.error = f"Planner classification error: {exc}"

    return state


def route_intent(state: AgentState) -> str:
    return state.intent
