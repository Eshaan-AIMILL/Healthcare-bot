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
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.graph import compiled_graph
from app.core.state import AgentState
from app.core.security import verify_and_create_context
from app.utils.logger import logger

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "healthcare-bot"
    messages: list[ChatMessage]
    stream: bool = False
    role: str = "guest"  # fallback
    metadata: dict[str, Any] = {}


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


@router.get("/v1/models")
async def list_models():
    """
    Required by Open WebUI to populate the models dropdown.
    """
    return {
        "object": "list",
        "data": [
            {
                "id": "healthcare-bot",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "hospital"
            }
        ]
    }


@router.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completions(http_request: Request, request: ChatRequest) -> ChatResponse:
    
    # Log incoming headers for debugging Open WebUI auth passing
    logger.info(f"Incoming headers: {http_request.headers}")
    
    # Log raw body to see what Open WebUI actually sends!
    raw_body = await http_request.json()
    logger.info(f"Raw incoming body: {raw_body}")
    
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

    # Extract security context from metadata or from a special injected payload
    security_metadata = request.metadata
    
    # 1. Check for injected system message from Open WebUI Filter (Method A)
    for i, msg in enumerate(conversation_history):
        if msg["role"] == "system" and msg["content"].startswith("SECURITY_CONTEXT:"):
            try:
                sec_data = json.loads(msg["content"].replace("SECURITY_CONTEXT:", "", 1))
                security_metadata = {
                    "security_context": sec_data.get("context"),
                    "security_signature": sec_data.get("signature")
                }
                # Remove it so the LLM doesn't see it
                conversation_history.pop(i)
                break
            except Exception as e:
                logger.error(f"Failed to parse injected system security context: {e}")

    # 2. Check for injected payload appended to the last user message (Method B)
    if not security_metadata:
        for i in range(len(conversation_history) - 1, -1, -1):
            msg = conversation_history[i]
            if msg["role"] == "user" and "[SECURITY_CONTEXT:" in msg["content"]:
                try:
                    content_str = msg["content"]
                    start_idx = content_str.rfind("[SECURITY_CONTEXT:")
                    json_str = content_str[start_idx + len("[SECURITY_CONTEXT:"):]
                    if json_str.endswith("]"):
                        json_str = json_str[:-1]
                    
                    sec_data = json.loads(json_str)
                    security_metadata = {
                        "security_context": sec_data.get("context"),
                        "security_signature": sec_data.get("signature")
                    }
                    
                    clean_content = content_str[:start_idx].strip()
                    conversation_history[i]["content"] = clean_content
                    
                    if i == len(conversation_history) - 1:
                        latest_query = clean_content
                        
                    break
                except Exception as e:
                    logger.error(f"Failed to parse injected user security context: {e}")

    security_context = verify_and_create_context(security_metadata)
    if not security_context:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={
            "status": "denied",
            "reason": "Insufficient permissions or spoofed role. Access blocked.",
            "required_role": "Valid OpenWebUI Session",
            "debug_metadata_received": security_metadata
        })

    initial_state = AgentState(
        query=latest_query,
        role=security_context.enterprise_role,
        security_context=security_context,
        messages=conversation_history,
    )

    logger.info(f"Processing query: {latest_query!r} by user {security_context.user_id} ({security_context.enterprise_role})")

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
