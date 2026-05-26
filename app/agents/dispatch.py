import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.utils.prompts import DISPATCH_SYSTEM, DISPATCH_USER
from app.utils.logger import logger
from app.config import settings
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from app.db.session import AsyncSessionLocal


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


async def dispatch_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        sql_rows = await run_text2sql(state.query, db)

    if sql_rows and "overall_on_time_rate_pct" in sql_rows[0]:
        state.sql_rows = sql_rows
        state.agent_result = {
            "analysis_type": "emergency_dispatch_on_time_rate",
            "row_count": len(sql_rows),
        }
        logger.info("Dispatch agent completed deterministic aggregate analysis.")
        return state

    if sql_rows and {"delivery_id", "route_id", "delay_minutes"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.agent_result = {
            "analysis_type": "weekly_delayed_deliveries",
            "row_count": len(sql_rows),
        }
        logger.info("Dispatch agent completed deterministic delayed deliveries analysis.")
        return state

    llm = _build_llm()
    messages = [
        SystemMessage(content=DISPATCH_SYSTEM),
        HumanMessage(
            content=DISPATCH_USER.format(
                sql_rows=json.dumps(sql_rows[:15], default=str),
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
        f"Dispatch agent completed. sla_risk={state.agent_result.get('sla_risk')}"
    )
    return state
