
import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.tools.rag_retriever import retrieve_context
from app.utils.prompts import BILLING_SYSTEM, BILLING_USER
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


async def billing_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        sql_rows = await run_text2sql(state.query, db)

    if sql_rows and "overall_rejection_rate_pct" in sql_rows[0]:
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "claim_rejection_rate_by_icd10",
            "row_count": len(sql_rows),
        }
        logger.info("Billing agent completed deterministic aggregate analysis.")
        return state

    if sql_rows and {"payer_name", "rejection_rate_pct", "top_error_type"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "payer_rejection_rate_breakdown",
            "row_count": len(sql_rows),
        }
        logger.info("Billing agent completed deterministic payer rejection analysis.")
        return state

    if sql_rows and {"risk_domain", "domain_critical_count", "domain_rank"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "cross_domain_critical_risk_dashboard",
            "row_count": len(sql_rows),
        }
        logger.info("Billing agent completed deterministic cross-domain dashboard analysis.")
        return state

    if sql_rows and {"claim_id", "rejection_risk", "rejection_risk_score"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "coding_error_claim_risk_classification",
            "row_count": len(sql_rows),
        }
        logger.info("Billing agent completed deterministic claim risk classification.")
        return state

    rag_context = await retrieve_context(state.query, domain="billing", top_k=5)

    llm = _build_llm()
    messages = [
        SystemMessage(content=BILLING_SYSTEM),
        HumanMessage(
            content=BILLING_USER.format(
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
        logger.error(f"Billing agent: LLM returned non-JSON: {exc}")
        state.error = f"Billing agent parse error: {exc}"
        state.agent_result = {}

    state.sql_rows = sql_rows
    state.rag_context = rag_context
    logger.info(f"Billing agent completed. rejection_risk={state.agent_result.get('rejection_risk')}")
    return state
