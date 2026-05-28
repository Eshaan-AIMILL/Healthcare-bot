import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.tools.rag_retriever import retrieve_context
from app.utils.prompts import PATIENT_SYSTEM, PATIENT_USER
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


async def patient_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        sql_rows = await run_text2sql(state.query, db, security_context=state.security_context, domain="patient")

    if sql_rows and "breached_complaint_count" in sql_rows[0]:
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "weekly_sla_breach_metrics",
            "row_count": len(sql_rows),
        }
        logger.info("Patient agent completed deterministic weekly SLA breach analysis.")
        return state

    if sql_rows and {"department", "sla_compliance_rate_pct"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "department_sla_compliance_ranking",
            "row_count": len(sql_rows),
        }
        logger.info("Patient agent completed deterministic department SLA ranking analysis.")
        return state

    rag_context = await retrieve_context(state.query, domain="patient", top_k=4)

    llm = _build_llm()
    messages = [
        SystemMessage(content=PATIENT_SYSTEM),
        HumanMessage(
            content=PATIENT_USER.format(
                sql_rows=json.dumps(sql_rows[:10], default=str),
                rag_context="\n---\n".join(rag_context),
                query=state.query,
            )
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
        state.agent_result = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.error(f"Patient agent parse error: {exc}")
        state.error = f"Patient agent parse error: {exc}"
        state.agent_result = {}

    state.sql_rows = sql_rows
    state.rag_context = rag_context
    logger.info(
        f"Patient agent completed. sla_breached={state.agent_result.get('sla_breached')}"
    )
    return state
