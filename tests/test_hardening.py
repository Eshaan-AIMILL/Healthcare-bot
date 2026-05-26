import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    ) as ac:
        yield ac


# ── SQL Injection Prevention ──────────────────────────────────────────────────

INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE patients; --",
    "\" OR \"1\"=\"1",
    "1; SELECT * FROM patients",
    "' UNION SELECT * FROM billing_claims --",
]

@pytest.mark.anyio
@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
async def test_billing_portal_injection_safe(client, payload):

    r = await client.get(f"/api/billing/claims?status={payload}")
    # Must return 200 or 422 (validation error) — never 500
    assert r.status_code in (200, 422), (
        f"Injection payload caused unexpected status {r.status_code}: {payload!r}"
    )
    if r.status_code == 200:
        body = r.text
        # Must not leak raw SQL keywords in response
        for keyword in ("DROP", "UNION", "SELECT *", "sqlite_master"):
            assert keyword.lower() not in body.lower(), (
                f"Potential SQL exposure for payload {payload!r}: found '{keyword}' in response"
            )


@pytest.mark.anyio
@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
async def test_compliance_portal_injection_safe(client, payload):
    r = await client.get(f"/api/compliance/findings?severity={payload}")
    assert r.status_code in (200, 422)


@pytest.mark.anyio
@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
async def test_dispatch_portal_injection_safe(client, payload):
    r = await client.get(f"/api/dispatch/routes?category={payload}")
    assert r.status_code in (200, 422)


# ── Portal Endpoints Return Correct Schema ────────────────────────────────────

@pytest.mark.anyio
async def test_billing_portal_schema(client):
    r = await client.get("/api/billing/claims?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "claims" in body
    assert isinstance(body["claims"], list)
    summary_keys = {"total_claims", "rejected", "coding_errors", "pending"}
    assert summary_keys.issubset(body["summary"].keys())


@pytest.mark.anyio
async def test_compliance_portal_schema(client):
    r = await client.get("/api/compliance/findings?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body and "findings" in body
    assert {"total_findings", "critical", "major", "open_findings"}.issubset(body["summary"].keys())


@pytest.mark.anyio
async def test_pharmacy_portal_schema(client):
    r = await client.get("/api/pharmacy/alerts")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "expiry_alerts" in body
    assert "reorder_alerts" in body


@pytest.mark.anyio
async def test_patient_portal_schema(client):
    r = await client.get("/api/patient/complaints?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body and "complaints" in body
    assert {"total_complaints", "sla_breached", "compensation_eligible"}.issubset(body["summary"].keys())


@pytest.mark.anyio
async def test_dispatch_portal_schema(client):
    r = await client.get("/api/dispatch/routes?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body and "routes" in body
    assert {"total_deliveries", "sla_breached", "on_time_delivered"}.issubset(body["summary"].keys())


@pytest.mark.anyio
async def test_vehicle_availability_schema(client):
    r = await client.get("/api/dispatch/vehicles")
    assert r.status_code == 200
    body = r.json()
    assert "vehicles" in body
    assert isinstance(body["vehicles"], list)


# ── Filter Parameters Work Correctly ─────────────────────────────────────────

@pytest.mark.anyio
async def test_billing_status_filter_rejected(client):
    r = await client.get("/api/billing/claims?status=Rejected&limit=50")
    assert r.status_code == 200
    for claim in r.json()["claims"]:
        assert claim["claim_status"] == "Rejected"


@pytest.mark.anyio
async def test_compliance_severity_filter(client):
    r = await client.get("/api/compliance/findings?severity=Critical&limit=50")
    assert r.status_code == 200
    for finding in r.json()["findings"]:
        assert finding["severity"] == "Critical"


@pytest.mark.anyio
async def test_patient_breached_only_filter(client):
    r = await client.get("/api/patient/complaints?breached_only=true&limit=50")
    assert r.status_code == 200
    for complaint in r.json()["complaints"]:
        assert complaint["sla_breached"] in (1, True)


@pytest.mark.anyio
async def test_dispatch_emergency_filter(client):
    r = await client.get("/api/dispatch/routes?category=Emergency&limit=50")
    assert r.status_code == 200
    for route in r.json()["routes"]:
        assert route["delivery_category"] == "Emergency"


# ── Graceful Handling of Limit Boundaries ────────────────────────────────────

@pytest.mark.anyio
async def test_limit_above_max_returns_422(client):
    r = await client.get("/api/billing/claims?limit=999")
    assert r.status_code == 422


@pytest.mark.anyio
async def test_pharmacy_window_variations(client):
    for days in [30, 60, 90]:
        r = await client.get(f"/api/pharmacy/alerts?alert_window_days={days}")
        assert r.status_code == 200


# ── Chat Endpoint Graceful Degradation ───────────────────────────────────────

@pytest.mark.anyio
async def test_chat_no_user_message_graceful(client):
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "healthcare-bot", "messages": [{"role": "system", "content": "hi"}]},
    )
    assert r.status_code == 400
    assert "detail" in r.json()


@pytest.mark.anyio
async def test_chat_empty_body_graceful(client):
    r = await client.post("/v1/chat/completions", json={})
    assert r.status_code == 422


# ── Dashboard Static File ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_dashboard_served(client):
    r = await client.get("/dashboard/index.html")
    assert r.status_code == 200
    assert "Healthcare Operations Intelligence" in r.text