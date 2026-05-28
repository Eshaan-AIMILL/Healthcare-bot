from dataclasses import dataclass, field
from typing import Any, Optional
from app.core.security import SecurityContext

@dataclass
class AgentState:

    query: str = ""
    role: str = "guest"
    security_context: Optional[SecurityContext] = None
    intent: str = ""

    # For cross-domain: list of all detected domain intents
    intents: list[str] = field(default_factory=list)

    # For cross-domain: per-domain agent results keyed by domain name
    domain_results: dict[str, Any] = field(default_factory=dict)

    # Confidence
    intent_confidence: float = 0.0

    # Conversation history
    messages: list[dict[str, str]] = field(default_factory=list)

    agent_result: dict[str, Any] = field(default_factory=dict)

    rag_context: list[str] = field(default_factory=list)

    sql_rows: list[dict[str, Any]] = field(default_factory=list)

    final_response: str = ""

    error: str = ""

