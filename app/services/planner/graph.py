import asyncio
import httpx
from copy import deepcopy
from langgraph.graph import StateGraph, END
from app.core.state import AgentState
from app.agents.planner import planner_node, route_intent
from app.agents.summarizer import summarizer_node
from app.services.common import state_to_payload_dict, dict_to_state
from app.config import settings
from app.utils.logger import logger
from app.services.cache.utils import build_cache_key

# Import local domain agents for robust hybrid-mode failover fallback
from app.agents.billing import billing_node
from app.agents.compliance import compliance_node
from app.agents.pharmacy import pharmacy_node
from app.agents.patient import patient_node
from app.agents.dispatch import dispatch_node

# ── HTTP Service Endpoints ────────────────────────────────────────────────────
SERVICE_URLS = {
    "billing": f"{settings.database_url.replace(settings.database_url, 'http://billing-service:8002') if 'billing-service' in settings.database_url else 'http://localhost:8002'}/chat/billing",
    "compliance": "http://compliance-service:8003/chat/compliance",
    "pharmacy": "http://pharmacy-service:8004/chat/pharmacy",
    "patient": "http://patient-service:8005/chat/patient",
    "dispatch": "http://dispatch-service:8006/chat/dispatch",
}

def get_service_url(domain: str) -> str:
    # Read URLs dynamically from settings overrides
    if domain == "billing":
        return f"{settings.billing_service_url}/chat/billing"
    elif domain == "compliance":
        return f"{settings.compliance_service_url}/chat/compliance"
    elif domain == "pharmacy":
        return f"{settings.pharmacy_service_url}/chat/pharmacy"
    elif domain == "patient":
        return f"{settings.patient_service_url}/chat/patient"
    elif domain == "dispatch":
        return f"{settings.dispatch_service_url}/chat/dispatch"
    elif domain == "cache":
        return settings.cache_service_url
    return ""

async def _post_to_service(url: str, state: AgentState) -> AgentState:
    payload = state_to_payload_dict(state)
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return dict_to_state(response.json())

# ── Cache Nodes and Routing ───────────────────────────────────────────────────

async def cache_check_node(state: AgentState) -> AgentState:
    if not settings.cache_enabled:
        return state
    
    role = "guest"
    if state.security_context:
        role = state.security_context.enterprise_role or "guest"
    elif state.role:
        role = state.role
        
    cache_key = build_cache_key(state.query, state.intent, role)
    url = f"{get_service_url('cache')}/cache/get"
    
    logger.info(f"Checking Response Cache for key prefix: {cache_key[:8]}...")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json={"cache_key": cache_key})
            if response.status_code == 200:
                data = response.json()
                if data.get("hit"):
                    logger.info("Response Cache HIT!")
                    state.final_response = data["response"]
                    return state
            logger.info("Response Cache MISS or error response.")
    except Exception as exc:
        logger.warning(f"Cache service check failed: {exc}. Continuing normal execution.")
        
    return state

async def cache_store_node(state: AgentState) -> AgentState:
    if not settings.cache_enabled or state.error or not state.final_response:
        return state
        
    role = "guest"
    if state.security_context:
        role = state.security_context.enterprise_role or "guest"
    elif state.role:
        role = state.role
        
    cache_key = build_cache_key(state.query, state.intent, role)
    url = f"{get_service_url('cache')}/cache/set"
    
    logger.info(f"Storing response in Cache for key prefix: {cache_key[:8]}...")
    try:
        domain = state.intent
        payload = {
            "cache_key": cache_key,
            "response": state.final_response,
            "domain": domain
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info("Successfully cached the response.")
            else:
                logger.warning(f"Cache service set returned status: {response.status_code}")
    except Exception as exc:
        logger.warning(f"Failed to store response in Cache: {exc}")
        
    return state

def route_cache(state: AgentState) -> str:
    if state.final_response:
        logger.info("Routing directly to END due to Cache HIT.")
        return "hit"
    return state.intent

# ── HTTP Graph Nodes with Local Fallback ───────────────────────────────────────

async def billing_http_node(state: AgentState) -> AgentState:
    url = get_service_url("billing")
    logger.info(f"Routing billing request to HTTP: {url}")
    try:
        return await _post_to_service(url, state)
    except Exception as exc:
        logger.warning(f"Billing microservice not reachable at {url} ({exc}). Falling back to local execution.")
        return await billing_node(state)

async def compliance_http_node(state: AgentState) -> AgentState:
    url = get_service_url("compliance")
    logger.info(f"Routing compliance request to HTTP: {url}")
    try:
        return await _post_to_service(url, state)
    except Exception as exc:
        logger.warning(f"Compliance microservice not reachable at {url} ({exc}). Falling back to local execution.")
        return await compliance_node(state)

async def pharmacy_http_node(state: AgentState) -> AgentState:
    url = get_service_url("pharmacy")
    logger.info(f"Routing pharmacy request to HTTP: {url}")
    try:
        return await _post_to_service(url, state)
    except Exception as exc:
        logger.warning(f"Pharmacy microservice not reachable at {url} ({exc}). Falling back to local execution.")
        return await pharmacy_node(state)

async def patient_http_node(state: AgentState) -> AgentState:
    url = get_service_url("patient")
    logger.info(f"Routing patient request to HTTP: {url}")
    try:
        return await _post_to_service(url, state)
    except Exception as exc:
        logger.warning(f"Patient microservice not reachable at {url} ({exc}). Falling back to local execution.")
        return await patient_node(state)

async def dispatch_http_node(state: AgentState) -> AgentState:
    url = get_service_url("dispatch")
    logger.info(f"Routing dispatch request to HTTP: {url}")
    try:
        return await _post_to_service(url, state)
    except Exception as exc:
        logger.warning(f"Dispatch microservice not reachable at {url} ({exc}). Falling back to local execution.")
        return await dispatch_node(state)

# ── Parallel HTTP Fan-Out with Local Fallback ─────────────────────────────────

async def _run_single_domain_http(domain: str, base_state: AgentState) -> tuple[str, AgentState]:
    url = get_service_url(domain)
    try:
        domain_state = deepcopy(base_state)
        domain_state.intent = domain
        domain_state.intents = [domain]
        result_state = await _post_to_service(url, domain_state)
        logger.info(f"HTTP Cross-domain: '{domain}' completed successfully.")
        return domain, result_state
    except Exception as exc:
        logger.warning(f"Cross-domain HTTP: '{domain}' not reachable at {url} ({exc}). Falling back to local execution.")
        try:
            domain_state = deepcopy(base_state)
            domain_state.intent = domain
            domain_state.intents = [domain]
            if domain == "billing":
                result_state = await billing_node(domain_state)
            elif domain == "compliance":
                result_state = await compliance_node(domain_state)
            elif domain == "pharmacy":
                result_state = await pharmacy_node(domain_state)
            elif domain == "patient":
                result_state = await patient_node(domain_state)
            elif domain == "dispatch":
                result_state = await dispatch_node(domain_state)
            else:
                raise ValueError(f"Unknown domain: {domain}")
            return domain, result_state
        except Exception as local_exc:
            logger.error(f"Local fallback execution failed for '{domain}': {local_exc}")
            failure_state = deepcopy(base_state)
            failure_state.error = f"Domain '{domain}' encountered an error: {str(local_exc)}"
            failure_state.sql_rows = []
            failure_state.agent_result = {}
            return domain, failure_state

async def cross_domain_http_node(state: AgentState) -> AgentState:
    domains = [d for d in state.intents if d in SERVICE_URLS]
    if not domains:
        logger.error("cross_domain_http_node called but no valid domains found.")
        state.error = "No valid domains found for cross-domain query."
        state.intent = "unauthorized"
        return state

    logger.info(f"HTTP Cross-domain fan-out: running {domains} in parallel.")
    tasks = [_run_single_domain_http(domain, state) for domain in domains]
    results = await asyncio.gather(*tasks)

    combined_domain_results = {}
    combined_sql_rows = []
    combined_agent_result = {}

    for domain, result_state in results:
        domain_data = {
            "agent_result": result_state.agent_result,
            "sql_rows": result_state.sql_rows[:5],
            "error": result_state.error or None,
        }
        combined_domain_results[domain] = domain_data
        combined_sql_rows.extend(result_state.sql_rows[:5])
        if result_state.agent_result:
            combined_agent_result[domain] = result_state.agent_result

    state.domain_results = combined_domain_results
    state.sql_rows = combined_sql_rows
    state.agent_result = combined_agent_result

    logger.info(f"HTTP Cross-domain merge complete.")
    return state

# ── Build HTTP Graph ──────────────────────────────────────────────────────────

def build_http_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Register local and HTTP-forwarding nodes
    graph.add_node("planner", planner_node)
    graph.add_node("cache_check", cache_check_node)
    graph.add_node("billing", billing_http_node)
    graph.add_node("compliance", compliance_http_node)
    graph.add_node("pharmacy", pharmacy_http_node)
    graph.add_node("patient", patient_http_node)
    graph.add_node("dispatch", dispatch_http_node)
    graph.add_node("cross_domain", cross_domain_http_node)
    graph.add_node("summarizer", summarizer_node)
    graph.add_node("cache_store", cache_store_node)

    graph.set_entry_point("planner")
    
    graph.add_edge("planner", "cache_check")

    graph.add_conditional_edges(
        "cache_check",
        route_cache,
        {
            "hit": END,
            "billing": "billing",
            "compliance": "compliance",
            "pharmacy": "pharmacy",
            "patient": "patient",
            "dispatch": "dispatch",
            "cross_domain": "cross_domain",
            "unauthorized": "summarizer",
            "general": "summarizer",
        },
    )

    for domain in ("billing", "compliance", "pharmacy", "patient", "dispatch"):
        graph.add_edge(domain, "summarizer")

    graph.add_edge("cross_domain", "summarizer")
    graph.add_edge("summarizer", "cache_store")
    graph.add_edge("cache_store", END)

    return graph.compile()

compiled_http_graph = build_http_graph()
