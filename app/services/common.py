from typing import Any, Optional
from pydantic import BaseModel
from app.core.state import AgentState
from app.core.security import SecurityContext

class SecurityContextPayload(BaseModel):
    user_id: str
    email: str
    openwebui_role: str
    enterprise_role: str
    timestamp: int

class AgentStatePayload(BaseModel):
    query: str = ""
    role: str = "guest"
    security_context: Optional[SecurityContextPayload] = None
    intent: str = ""
    intents: list[str] = []
    domain_results: dict[str, Any] = {}
    intent_confidence: float = 0.0
    messages: list[dict[str, str]] = []
    agent_result: dict[str, Any] = {}
    rag_context: list[str] = []
    sql_rows: list[dict[str, Any]] = []
    final_response: str = ""
    error: str = ""

def payload_to_state(payload: AgentStatePayload) -> AgentState:
    sec_ctx = None
    if payload.security_context:
        sec_ctx = SecurityContext(
            user_id=payload.security_context.user_id,
            email=payload.security_context.email,
            openwebui_role=payload.security_context.openwebui_role,
            enterprise_role=payload.security_context.enterprise_role,
            timestamp=payload.security_context.timestamp
        )
    return AgentState(
        query=payload.query,
        role=payload.role,
        security_context=sec_ctx,
        intent=payload.intent,
        intents=payload.intents,
        domain_results=payload.domain_results,
        intent_confidence=payload.intent_confidence,
        messages=payload.messages,
        agent_result=payload.agent_result,
        rag_context=payload.rag_context,
        sql_rows=payload.sql_rows,
        final_response=payload.final_response,
        error=payload.error
    )

def state_to_payload_dict(state: AgentState) -> dict:
    sec_ctx_dict = None
    if state.security_context:
        sec_ctx_dict = {
            "user_id": state.security_context.user_id,
            "email": state.security_context.email,
            "openwebui_role": state.security_context.openwebui_role,
            "enterprise_role": state.security_context.enterprise_role,
            "timestamp": state.security_context.timestamp
        }
    return {
        "query": state.query,
        "role": state.role,
        "security_context": sec_ctx_dict,
        "intent": state.intent,
        "intents": state.intents,
        "domain_results": state.domain_results,
        "intent_confidence": state.intent_confidence,
        "messages": state.messages,
        "agent_result": state.agent_result,
        "rag_context": state.rag_context,
        "sql_rows": state.sql_rows,
        "final_response": state.final_response,
        "error": state.error
    }

def dict_to_state(data: dict) -> AgentState:
    payload = AgentStatePayload(**data)
    return payload_to_state(payload)
