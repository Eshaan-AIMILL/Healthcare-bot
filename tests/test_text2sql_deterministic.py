import pytest

from app.db.session import AsyncSessionLocal
from app.tools.text2sql import run_text2sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_keys"),
    [
        (
            "Which payer has the highest claim rejection rate and what error types are most common?",
            {"payer_name", "rejection_rate_pct", "top_error_type"},
        ),
        (
            "How many appointment complaints breached SLA this week and what is the average delay in minutes?",
            {"breached_complaint_count", "avg_delay_minutes_over_sla"},
        ),
        (
            "Give me a risk dashboard across billing, compliance, pharmacy, patient support, and dispatch — highlight everything at Critical severity.",
            {"risk_domain", "domain_critical_count", "domain_rank"},
        ),
        (
            "Show me all delayed deliveries from this week and recommend alternative routes.",
            {"delivery_id", "route_id", "delay_minutes"},
        ),
    ],
)
async def test_deterministic_text2sql_queries(query: str, expected_keys: set[str]):
    async with AsyncSessionLocal() as db:
        rows = await run_text2sql(query, db)

    assert rows, f"Expected rows for query: {query}"
    assert expected_keys.issubset(rows[0].keys())
