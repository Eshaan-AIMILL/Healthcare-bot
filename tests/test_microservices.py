import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from fastapi.testclient import TestClient

from app.core.state import AgentState
from app.core.security import SecurityContext
from app.services.common import (
    AgentStatePayload,
    SecurityContextPayload,
    payload_to_state,
    state_to_payload_dict,
    dict_to_state,
)

# Import FastAPIs from services
from app.services.billing.main import app as billing_app
from app.services.compliance.main import app as compliance_app
from app.services.pharmacy.main import app as pharmacy_app
from app.services.patient.main import app as patient_app
from app.services.dispatch.main import app as dispatch_app
from app.services.planner.graph import (
    compiled_http_graph,
    get_service_url,
    billing_http_node,
    compliance_http_node,
    pharmacy_http_node,
    patient_http_node,
    dispatch_http_node,
    cross_domain_http_node,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── Test Serialization Contracts ──────────────────────────────────────────────

def test_serialization_roundtrip():
    sec_ctx = SecurityContext(
        user_id="user123",
        email="user@test.com",
        openwebui_role="user",
        enterprise_role="billing_officer",
        timestamp=123456789,
    )
    original_state = AgentState(
        query="What is my bill?",
        role="billing_officer",
        security_context=sec_ctx,
        intent="billing",
        messages=[{"role": "user", "content": "What is my bill?"}],
    )

    # State to Dict
    payload_dict = state_to_payload_dict(original_state)
    assert payload_dict["query"] == "What is my bill?"
    assert payload_dict["security_context"]["user_id"] == "user123"
    assert payload_dict["security_context"]["enterprise_role"] == "billing_officer"

    # Dict to State
    restored_state = dict_to_state(payload_dict)
    assert restored_state.query == original_state.query
    assert restored_state.role == original_state.role
    assert restored_state.security_context.user_id == sec_ctx.user_id
    assert restored_state.security_context.enterprise_role == sec_ctx.enterprise_role
    assert restored_state.intent == original_state.intent
    assert len(restored_state.messages) == 1
    assert restored_state.messages[0]["content"] == "What is my bill?"


# ── Test Microservices GET /health and POST /chat ──────────────────────────────

def test_microservices_healthcheck_endpoints():
    for name, service_app in [
        ("billing", billing_app),
        ("compliance", compliance_app),
        ("pharmacy", pharmacy_app),
        ("patient", patient_app),
        ("dispatch", dispatch_app),
    ]:
        client = TestClient(service_app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == name


# ── Test HTTP Graph Routing URLs ──────────────────────────────────────────────

def test_service_url_generation():
    assert get_service_url("billing").endswith("/chat/billing")
    assert get_service_url("compliance").endswith("/chat/compliance")
    assert get_service_url("pharmacy").endswith("/chat/pharmacy")
    assert get_service_url("patient").endswith("/chat/patient")
    assert get_service_url("dispatch").endswith("/chat/dispatch")


# ── Test Graph Nodes HTTP mock integration ────────────────────────────────────

@pytest.mark.anyio
@patch("app.services.planner.graph._post_to_service")
async def test_billing_http_node_triggers_correct_post(mock_post):
    state = AgentState(query="test billing request")
    mock_returned_state = AgentState(query="test billing request", final_response="Mocked Billing Response")
    mock_post.return_value = mock_returned_state

    result = await billing_http_node(state)
    mock_post.assert_called_once_with(get_service_url("billing"), state)
    assert result.final_response == "Mocked Billing Response"


@pytest.mark.anyio
@patch("app.services.planner.graph._post_to_service")
async def test_cross_domain_http_node_parallel_routing(mock_post):
    state = AgentState(
        query="Check pharmacy and billing issues.",
        intents=["pharmacy", "billing"]
    )
    
    def side_effect(url, s):
        if "pharmacy" in url:
            return AgentState(query=s.query, agent_result={"pharmacy": "expired items"}, sql_rows=[{"id": 1}])
        if "billing" in url:
            return AgentState(query=s.query, agent_result={"billing": "pending audit"}, sql_rows=[{"id": 2}])
        return s

    mock_post.side_effect = side_effect

    result = await cross_domain_http_node(state)
    
    assert mock_post.call_count == 2
    assert "pharmacy" in result.domain_results
    assert "billing" in result.domain_results
    assert result.domain_results["pharmacy"]["agent_result"] == {"pharmacy": "expired items"}
    assert result.domain_results["billing"]["agent_result"] == {"billing": "pending audit"}
    assert len(result.sql_rows) == 2
