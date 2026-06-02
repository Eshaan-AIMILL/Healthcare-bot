import pytest
import json
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.services.cache.utils import normalize_query, build_cache_key, get_domain_ttl
from app.services.cache.main import app as cache_app
from app.core.state import AgentState
from app.core.security import SecurityContext
from app.services.planner.graph import compiled_http_graph

def test_normalize_query():
    assert normalize_query("  Hello World!  ") == "hello world"
    assert normalize_query("What is the stock level???") == "what is the stock level"
    assert normalize_query("   Multiple   Spaces   ") == "multiple spaces"
    assert normalize_query("") == ""

def test_build_cache_key():
    key1 = build_cache_key("What is the stock level?", "pharmacy", "pharmacist")
    key2 = build_cache_key("  what is the stock level  ", "pharmacy", "pharmacist")
    key3 = build_cache_key("what is the stock level", "billing", "pharmacist")
    
    assert key1 == key2
    assert key1 != key3

def test_get_domain_ttl():
    assert get_domain_ttl("pharmacy") == 300
    assert get_domain_ttl("dispatch") == 180
    assert get_domain_ttl("cross_domain") == 300
    assert get_domain_ttl("invalid_domain") == 1800

@patch("app.services.cache.main.redis_client")
def test_cache_endpoints(mock_redis):
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.keys = AsyncMock(return_value=[])
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.ping = AsyncMock(return_value=True)
    
    client = TestClient(cache_app)
    
    # 1. Health check
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    
    # 2. Get miss
    response = client.post("/cache/get", json={"cache_key": "somekey"})
    assert response.status_code == 200
    assert response.json()["hit"] is False
    
    # 3. Set
    response = client.post("/cache/set", json={
        "cache_key": "somekey",
        "response": "cached response text",
        "domain": "pharmacy"
    })
    assert response.status_code == 200
    assert response.json()["success"] is True
    
    # 4. Get hit
    mock_redis.get = AsyncMock(return_value=json.dumps({
        "response": "cached response text",
        "metadata": {"domain": "pharmacy", "ttl": 300}
    }))
    
    response = client.post("/cache/get", json={"cache_key": "somekey"})
    assert response.status_code == 200
    assert response.json()["hit"] is True
    assert response.json()["response"] == "cached response text"

@pytest.mark.asyncio
async def test_graph_cache_hit():
    initial_state = AgentState(
        query="What is the stock level?",
        role="admin",
        security_context=SecurityContext(
            user_id="user1",
            email="admin@hospital.local",
            openwebui_role="admin",
            enterprise_role="admin",
            timestamp=123456
        ),
        intent="pharmacy"
    )
    
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"hit": True, "response": "Cached stock details"}
    
    with patch("httpx.AsyncClient.post", return_value=mock_response):
        mock_planner_llm = AsyncMock()
        mock_planner_llm.content = json.dumps({
            "intents": ["pharmacy"],
            "is_cross_domain": False,
            "confidence": 1.0
        })
        
        with patch("langchain_ollama.ChatOllama.ainvoke", return_value=mock_planner_llm):
            final_state = await compiled_http_graph.ainvoke(initial_state)
            assert final_state.get("final_response") == "Cached stock details"
            assert not final_state.get("error")

@pytest.mark.asyncio
async def test_graph_cache_miss_and_store():
    initial_state = AgentState(
        query="What is the stock level?",
        role="admin",
        security_context=SecurityContext(
            user_id="user1",
            email="admin@hospital.local",
            openwebui_role="admin",
            enterprise_role="admin",
            timestamp=123456
        ),
        intent="pharmacy"
    )
    
    async def mock_post(url, **kwargs):
        resp = AsyncMock()
        resp.status_code = 200
        if "cache/get" in url:
            resp.json = lambda: {"hit": False}
        elif "cache/set" in url:
            resp.json = lambda: {"success": True}
        elif "chat/pharmacy" in url:
            resp.json = lambda: {
                "query": "What is the stock level?",
                "role": "admin",
                "intent": "pharmacy",
                "agent_result": {"status": "success"},
                "sql_rows": [{"item": "aspirin", "stock": 50}]
            }
        return resp
        
    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        mock_planner_llm = AsyncMock()
        mock_planner_llm.content = json.dumps({
            "intents": ["pharmacy"],
            "is_cross_domain": False,
            "confidence": 1.0
        })
        
        mock_summarizer_llm = AsyncMock()
        mock_summarizer_llm.content = "Summarized stock: 50 aspirin remaining."
        
        with patch("langchain_ollama.ChatOllama.ainvoke", side_effect=[mock_planner_llm, mock_summarizer_llm]):
            final_state = await compiled_http_graph.ainvoke(initial_state)
            assert final_state.get("final_response") == "Summarized stock: 50 aspirin remaining."
