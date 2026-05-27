"""
Dispatch Agent
Uses the production dispatch engine for deterministic rule-based checks,
then passes the structured assessment to the LLM for explanation.

Flow:
  1. Text2SQL  → fetch delivery rows from DB
  2. dispatch_engine → run all production checks (compatibility, ETA,
     SLA prediction, fatigue, trade-off scoring)
  3. LLM       → explain the assessment in plain English
  4. Summarizer→ format final response
"""
import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.agents.dispatch_engine import (
    run_dispatch_assessment,
    sequence_multi_stop,
    check_driver_fatigue,
    score_route_options,
    compute_adjusted_eta,
    get_traffic_factor,
    SLA_WINDOWS,
)
from app.utils.prompts import DISPATCH_SYSTEM, DISPATCH_USER
from app.utils.logger import logger
from app.config import settings
from app.db.session import AsyncSessionLocal
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from datetime import datetime


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


async def dispatch_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        # Step 1 — fetch relevant delivery rows via Text2SQL
        sql_rows = await run_text2sql(state.query, db)

        # Step 2 — run production engine checks on up to 5 deliveries
        assessments = []
        for row in sql_rows[:5]:
            delivery_id = row.get("delivery_id")
            if delivery_id:
                assessment = await run_dispatch_assessment(delivery_id, db)
                assessments.append(assessment)

    # Step 3 — pass both raw SQL rows and engine assessments to LLM
    enriched_context = {
        "sql_rows":    sql_rows[:15],
        "assessments": assessments,
        "engine_summary": {
            "total_assessed":         len(assessments),
            "critical_risk_count":    sum(1 for a in assessments if a.get("overall_risk") == "Critical"),
            "sla_breach_predicted":   sum(1 for a in assessments if a.get("flags", {}).get("sla_breach_predicted")),
            "incompatible_vehicles":  sum(1 for a in assessments if a.get("flags", {}).get("vehicle_incompatible")),
            "fatigued_drivers":       sum(1 for a in assessments if a.get("flags", {}).get("driver_fatigued")),
        },
    }

    llm = _build_llm()
    messages = [
        SystemMessage(content=DISPATCH_SYSTEM),
        HumanMessage(
            content=DISPATCH_USER.format(
                sql_rows=json.dumps(enriched_context, default=str, indent=2),
                query=state.query,
            )
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
        state.agent_result = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.error(f"Dispatch agent parse error: {exc}")
        state.error = f"Dispatch agent parse error: {exc}"
        state.agent_result = {}

    state.sql_rows = sql_rows
    logger.info(
        f"Dispatch agent completed. "
        f"assessments={len(assessments)} "
        f"critical={enriched_context['engine_summary']['critical_risk_count']}"
    )
    return state