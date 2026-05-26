from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentState:

    query: str = ""

    # Planner decision: which domain agent to invoke
    intent: str = "" 

    # Confidence
    intent_confidence: float = 0.0

    # Conversation history
    messages: list[dict[str, str]] = field(default_factory=list)

    agent_result: dict[str, Any] = field(default_factory=dict)

    rag_context: list[str] = field(default_factory=list)

    sql_rows: list[dict[str, Any]] = field(default_factory=list)

    final_response: str = ""

    error: str = ""
