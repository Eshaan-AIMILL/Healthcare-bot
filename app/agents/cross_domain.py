"""
cross_domain.py
---------------
Fan-out orchestrator that runs multiple domain agents in parallel for cross-domain queries.

Flow:
  1. Receives state with state.intents = ["billing", "pharmacy", ...]
  2. For each domain, calls the corresponding agent node with a per-domain copy of state
  3. Collects all results with asyncio.gather (errors in one domain don't crash others)
  4. Merges results into state.domain_results and a combined SQL/agent_result for the summarizer
"""

import asyncio
import json
from copy import deepcopy

from app.core.state import AgentState
from app.utils.logger import logger

# Lazy imports to avoid circular deps — each domain module is imported inside the function
DOMAIN_NODE_MAP = {
    "billing":    "app.agents.billing:billing_node",
    "compliance": "app.agents.compliance:compliance_node",
    "pharmacy":   "app.agents.pharmacy:pharmacy_node",
    "patient":    "app.agents.patient:patient_node",
    "dispatch":   "app.agents.dispatch:dispatch_node",
}


def _import_node(domain: str):
    """Dynamically import a domain node function to avoid circular imports."""
    import importlib
    module_path, fn_name = DOMAIN_NODE_MAP[domain].rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, fn_name)


async def _run_single_domain(domain: str, base_state: AgentState) -> tuple[str, AgentState]:
    """Run a single domain agent on a deep copy of state. Returns (domain, result_state)."""
    try:
        node_fn = _import_node(domain)
        # Give each domain its own isolated state copy
        domain_state = deepcopy(base_state)
        domain_state.intent = domain
        domain_state.intents = [domain]
        result_state = await node_fn(domain_state)
        logger.info(f"Cross-domain: '{domain}' agent completed successfully.")
        return domain, result_state
    except Exception as exc:
        logger.error(f"Cross-domain: '{domain}' agent failed: {exc}")
        # Return a failure state so the merger can handle it gracefully
        failure_state = deepcopy(base_state)
        failure_state.error = f"Domain '{domain}' encountered an error: {str(exc)}"
        failure_state.sql_rows = []
        failure_state.agent_result = {}
        return domain, failure_state


async def cross_domain_node(state: AgentState) -> AgentState:
    """
    Fan-out: run all requested domain agents in parallel.
    Results are merged into state.domain_results and state.agent_result.
    """
    domains = [d for d in state.intents if d in DOMAIN_NODE_MAP]

    if not domains:
        logger.error("cross_domain_node called but no valid domains in state.intents.")
        state.error = "No valid domains found for cross-domain query."
        state.intent = "unauthorized"
        return state

    logger.info(f"Cross-domain fan-out: running {domains} in parallel.")

    # Run all domain agents concurrently
    tasks = [_run_single_domain(domain, state) for domain in domains]
    results: list[tuple[str, AgentState]] = await asyncio.gather(*tasks)

    # Merge results
    combined_domain_results = {}
    combined_sql_rows = []
    combined_agent_result = {}

    for domain, result_state in results:
        domain_data = {
            "agent_result": result_state.agent_result,
            "sql_rows": result_state.sql_rows[:5],  # limit per-domain rows to keep payload manageable
            "error": result_state.error or None,
        }
        combined_domain_results[domain] = domain_data
        combined_sql_rows.extend(result_state.sql_rows[:5])
        if result_state.agent_result:
            combined_agent_result[domain] = result_state.agent_result

    state.domain_results = combined_domain_results
    state.sql_rows = combined_sql_rows
    state.agent_result = combined_agent_result

    logger.info(f"Cross-domain merge complete. Domains collected: {list(combined_domain_results.keys())}")
    return state
