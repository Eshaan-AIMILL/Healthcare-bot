"""
Chat API Router
Exposes an OpenAI-compatible /v1/chat/completions endpoint so Open WebUI
can connect to it without any custom configuration.
"""
import time
import uuid
import json
from collections.abc import Mapping
from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.graph import compiled_graph
from app.core.state import AgentState
from app.utils.logger import logger

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "healthcare-bot"
    messages: list[ChatMessage]
    stream: bool = False
    role: str = "guest"  # admin | reception | guest


class ChatChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatChoice]


def _state_value(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, Mapping):
        return state.get(key, default)
    return getattr(state, key, default)


def _metadata_prompt_response(query: str) -> str | None:
    normalized = query.lower()
    if not normalized.startswith("### task:"):
        return None

    if "follow_ups" in normalized and "suggest 3-5 relevant follow-up" in normalized:
        follow_ups = [
            "Which items need immediate action?",
            "Can you break this down by department or route?",
            "What changed compared with last month?",
            "Which records are still open or unresolved?",
        ]

        if "claim rejection" in normalized or "icd-10" in normalized:
            follow_ups[0] = "Which ICD-10 rejection drivers have the highest financial impact?"
        if "dispatch" in normalized or "delivery" in normalized:
            follow_ups[1] = "Which emergency routes are causing the most SLA misses?"
        if "hipaa" in normalized:
            follow_ups[3] = "Which HIPAA findings are still open or in review?"
        if "expiring" in normalized or "drug" in normalized:
            follow_ups[2] = "Which expiring drugs have the highest cost exposure?"

        return json.dumps({"follow_ups": follow_ups})

    if '"title"' in normalized and "generate" in normalized and "title" in normalized:
        return json.dumps({"title": "Healthcare Operations Metrics"})

    if '"tags"' in normalized and "generate" in normalized and "tags" in normalized:
        return json.dumps({"tags": ["Health", "Healthcare Operations", "Analytics"]})

    return None


@router.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completions(request: ChatRequest) -> ChatResponse:
    
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found in request.")

    latest_query = user_messages[-1].content.strip()
    if not latest_query:
        raise HTTPException(status_code=400, detail="User message content is empty.")

    metadata_response = _metadata_prompt_response(latest_query)
    if metadata_response is not None:
        logger.info("Handled metadata prompt without graph execution.")
        return ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=metadata_response),
                )
            ],
        )

    conversation_history = [{"role": m.role, "content": m.content} for m in request.messages]

    initial_state = AgentState(
        query=latest_query,
        role=request.role,
        messages=conversation_history,
    )

    logger.info(f"Processing query: {latest_query!r}")

    try:
        final_state = await compiled_graph.ainvoke(initial_state)
        response_text = _state_value(final_state, "final_response") or "No response generated."
    except Exception as exc:
        logger.error(f"Graph execution failed: {exc}")
        response_text = (
            "The healthcare bot encountered an internal error. "
            "Please check the server logs and try again."
        )

    return ChatResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=request.model,
        choices=[
            ChatChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response_text),
            )
        ],
    )
