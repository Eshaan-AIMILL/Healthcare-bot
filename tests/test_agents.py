import asyncio
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.agents.planner import planner_node
from app.core.state import AgentState


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_models_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "healthcare-bot"


@pytest.mark.asyncio
async def test_chat_completions_returns_response():
    import hmac
    import hashlib
    import json
    import time
    from app.config import settings
    
    context_data = {
        "user_id": "test_admin",
        "email": "admin@localhost",
        "openwebui_role": "admin",
        "timestamp": int(time.time())
    }
    payload_str = json.dumps(context_data, separators=(',', ':'), sort_keys=True)
    signature = hmac.new(
        settings.get_secret_key().encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    sec_context = {
        "context": context_data,
        "signature": signature
    }
    system_msg = {"role": "system", "content": f"SECURITY_CONTEXT:{json.dumps(sec_context)}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=120.0) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "healthcare-bot",
                "messages": [
                    system_msg,
                    {"role": "user", "content": "Show me any billing claims with coding errors."}
                ],
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert data["choices"][0]["message"]["role"] == "assistant"
    content = data["choices"][0]["message"]["content"]
    assert len(content) > 20


# ── Planner routing tests ─────────────────────────────────────────────────────

ROUTING_CASES = [
    ("Check which ICD-10 codes are causing the most claim rejections", "billing"),
    ("List all Critical HIPAA violations from the last 30 days", "compliance"),
    ("Which drugs are expiring within 60 days?", "pharmacy"),
    ("How many appointment complaints breached SLA this week?", "patient"),
    ("What is the fastest route for our emergency delivery to AIIMS?", "dispatch"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_intent", ROUTING_CASES)
async def test_planner_routing(query: str, expected_intent: str):
    import time
    from app.core.security import SecurityContext
    
    context = SecurityContext(
        user_id="test_admin",
        email="admin@localhost",
        openwebui_role="admin",
        enterprise_role="admin",
        timestamp=int(time.time()),
    )
    
    state = AgentState(query=query, security_context=context, role="admin")
    result = await planner_node(state)
    assert result.intent == expected_intent, (
        f"Query '{query}' — expected intent='{expected_intent}', got='{result.intent}'"
    )
    assert result.intent_confidence >= 0.5
