import json
from app.core.state import AgentState
from app.tools.text2sql import run_text2sql
from app.tools.rag_retriever import retrieve_context
from app.utils.prompts import COMPLIANCE_SYSTEM, COMPLIANCE_USER
from app.utils.logger import logger
from app.config import settings
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from app.db.session import AsyncSessionLocal


def _build_llm() -> ChatOllama:
    return ChatOllama(model=settings.ollama_model, base_url=settings.ollama_base_url, temperature=0.0, format="json")


async def compliance_node(state: AgentState) -> AgentState:
    async with AsyncSessionLocal() as db:
        sql_rows = await run_text2sql(state.query, db, security_context=state.security_context, domain="compliance")

    if sql_rows and {"finding_id", "severity", "regulation_body"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "critical_hipaa_findings",
            "row_count": len(sql_rows),
        }
        logger.info("Compliance agent completed deterministic findings analysis.")
        return state

    if sql_rows and {"process_id", "patient_consent_obtained", "severity"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "consent_missing_processes",
            "row_count": len(sql_rows),
        }
        logger.info("Compliance agent completed deterministic consent-missing analysis.")
        return state

    if sql_rows and {"total_open_jci_violations", "department_open_count"}.issubset(sql_rows[0]):
        state.sql_rows = sql_rows
        state.rag_context = []
        state.agent_result = {
            "analysis_type": "open_jci_department_breakdown",
            "row_count": len(sql_rows),
        }
        logger.info("Compliance agent completed deterministic JCI open-violations analysis.")
        return state

    rag_context = await retrieve_context(state.query, domain="compliance", top_k=5)

    llm = _build_llm()
    messages = [
        SystemMessage(content=COMPLIANCE_SYSTEM),
        HumanMessage(content=COMPLIANCE_USER.format(
            sql_rows=json.dumps(sql_rows[:10], default=str),
            rag_context="\n---\n".join(rag_context),
            query=state.query,
        )),
    ]

    try:
        response = await llm.ainvoke(messages)
        state.agent_result = json.loads(response.content)
    except json.JSONDecodeError as exc:
        logger.error(f"Compliance agent parse error: {exc}")
        state.error = f"Compliance agent parse error: {exc}"
        state.agent_result = {}

    state.sql_rows = sql_rows
    state.rag_context = rag_context
    logger.info(f"Compliance agent completed. severity={state.agent_result.get('severity')}")
    return state
