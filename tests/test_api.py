import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


def sign_messages(messages_list):
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
        settings.openwebui_secret_key.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    sec_context = {
        "context": context_data,
        "signature": signature
    }
    system_msg = {"role": "system", "content": f"SECURITY_CONTEXT:{json.dumps(sec_context)}"}
    return [system_msg] + messages_list


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=120.0,
    ) as ac:
        yield ac


# ── Health & Models ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ollama_connected" in body
    assert "model" in body


@pytest.mark.anyio
async def test_models_list(client):
    r = await client.get("/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert any(m["id"] == "healthcare-bot" for m in body["data"])


# ── Chat completions — basic contract ─────────────────────────────────────────

@pytest.mark.anyio
async def test_chat_returns_assistant_message(client):
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": sign_messages([
                {"role": "user", "content": "Show me any rejected billing claims."}
            ]),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["object"] == "chat.completion"
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert len(choice["message"]["content"]) > 10
    assert choice["finish_reason"] == "stop"


@pytest.mark.anyio
async def test_chat_missing_user_message_returns_400(client):
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": [
                {"role": "system", "content": "You are a bot."}
            ],
        },
    )
    assert r.status_code == 400


@pytest.mark.anyio
async def test_chat_empty_content_returns_400(client):
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": [{"role": "user", "content": "   "}],
        },
    )
    assert r.status_code == 400


# ── Multi-turn conversation ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_multiturn_conversation(client):
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": sign_messages([
                {"role": "user", "content": "List drugs expiring within 30 days."},
                {"role": "assistant", "content": "Here are the drugs expiring soon..."},
                {"role": "user", "content": "Which of those need immediate reorder?"},
            ]),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["choices"][0]["message"]["content"]) > 10


# ── Domain routing via chat ───────────────────────────────────────────────────

DOMAIN_QUERIES = [
    (
        "billing",
        "What ICD-10 codes have the highest claim rejection rate this month?",
    ),
    (
        "compliance",
        "Show me all Critical HIPAA violations logged in the last 30 days.",
    ),
    (
        "pharmacy",
        "Which drugs are expiring within 60 days and what is our cost exposure?",
    ),
    (
        "patient",
        "How many appointment complaints breached SLA this week?",
    ),
    (
        "dispatch",
        "What is the on-time delivery rate for emergency dispatches?",
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize("domain,query", DOMAIN_QUERIES)
async def test_domain_queries_return_non_empty_response(client, domain, query):
    """Each domain query should produce a substantive non-empty response."""
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": sign_messages([{"role": "user", "content": query}]),
        },
    )
    assert r.status_code == 200, f"[{domain}] Expected 200, got {r.status_code}"
    content = r.json()["choices"][0]["message"]["content"]
    assert len(content) > 30, f"[{domain}] Response too short: {content!r}"


# ── Executive-level questions ─────────────────────────────────────────────────

EXECUTIVE_QUERIES = [
    "What is our overall claim rejection rate this month and which ICD-10 codes are driving it?",
    "Show me all Critical HIPAA violations from the last 30 days and their resolution status.",
    "Which drugs are expiring within 60 days and what is our total replacement cost exposure?",
    "How many appointment complaints breached SLA this week and what is the average delay in minutes?",
    "What is the on-time delivery rate for emergency medical dispatches and which routes underperform?",
]


@pytest.mark.anyio
@pytest.mark.parametrize("query", EXECUTIVE_QUERIES)
async def test_executive_queries(client, query):
    """Executive-level queries must return structured, substantive responses."""
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": sign_messages([{"role": "user", "content": query}]),
        },
    )
    assert r.status_code == 200
    content = r.json()["choices"][0]["message"]["content"]
    # Executive responses should be detailed
    assert len(content) > 100, f"Response too short for executive query:\n{query}\n\nGot: {content!r}"


# ── Response schema validation ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_response_schema_has_all_required_fields(client):
    r = await client.post(
        "/v1/chat/completions",
        json={
            "model": "healthcare-bot",
            "messages": sign_messages([{"role": "user", "content": "List billing claims under review."}]),
        },
    )
    body = r.json()
    # OpenAI-compatible schema
    assert "id" in body
    assert "object" in body
    assert "created" in body
    assert "model" in body
    assert "choices" in body
    choice = body["choices"][0]
    assert "index" in choice
    assert "message" in choice
    assert "role" in choice["message"]
    assert "content" in choice["message"]
    assert "finish_reason" in choice
