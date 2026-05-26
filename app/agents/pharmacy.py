import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.utils.prompts import PHARMACY_SYSTEM, PHARMACY_USER
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


async def pharmacy_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        sql_rows = await run_text2sql(state.query, db)

    if sql_rows and "total_cost_exposure" in sql_rows[0]:
        state.sql_rows = sql_rows
        state.agent_result = {
            "analysis_type": "drug_expiry_cost_exposure",
            "row_count": len(sql_rows),
        }
        logger.info("Pharmacy agent completed deterministic expiry analysis.")
        return state

    if sql_rows and "below_reorder_count" in sql_rows[0]:
        state.sql_rows = sql_rows
        state.agent_result = {
            "analysis_type": "drug_reorder_stockout_risk",
            "row_count": len(sql_rows),
        }
        logger.info("Pharmacy agent completed deterministic reorder analysis.")
        return state

    llm = _build_llm()
    messages = [
        SystemMessage(content=PHARMACY_SYSTEM),
        HumanMessage(
            content=PHARMACY_USER.format(
                sql_rows=json.dumps(sql_rows[:20], default=str),
                query=state.query,
            )
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
        state.agent_result = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.error(f"Pharmacy agent parse error: {exc}")
        state.error = f"Pharmacy agent parse error: {exc}"
        state.agent_result = {}

    state.sql_rows = sql_rows
    logger.info(
        f"Pharmacy agent completed. "
        f"expiry_alerts={len(state.agent_result.get('expiry_alerts', []))}"
    )
    return state
