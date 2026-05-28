from langgraph.graph import StateGraph, END
from app.core.state import AgentState
from app.agents.planner import planner_node, route_intent
from app.agents.billing import billing_node
from app.agents.compliance import compliance_node
from app.agents.pharmacy import pharmacy_node
from app.agents.patient import patient_node
from app.agents.dispatch import dispatch_node
from app.agents.cross_domain import cross_domain_node
from app.agents.summarizer import summarizer_node


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("planner", planner_node)
    graph.add_node("billing", billing_node)
    graph.add_node("compliance", compliance_node)
    graph.add_node("pharmacy", pharmacy_node)
    graph.add_node("patient", patient_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("cross_domain", cross_domain_node)
    graph.add_node("summarizer", summarizer_node)

    # Entry point
    graph.set_entry_point("planner")

    # Conditional routing: planner to domain agents
    graph.add_conditional_edges(
        "planner",
        route_intent,
        {
            "billing": "billing",
            "compliance": "compliance",
            "pharmacy": "pharmacy",
            "patient": "patient",
            "dispatch": "dispatch",
            "cross_domain": "cross_domain",   # NEW: fan-out route
            "unauthorized": "summarizer",
            "general": "summarizer",
        },
    )

    # All single-domain agents feed into the summarizer
    for domain in ("billing", "compliance", "pharmacy", "patient", "dispatch"):
        graph.add_edge(domain, "summarizer")

    # Cross-domain orchestrator also feeds into summarizer
    graph.add_edge("cross_domain", "summarizer")

    # Summarizer is the terminal node
    graph.add_edge("summarizer", END)

    return graph.compile()


# Compiled graph instance used by the API
compiled_graph = build_graph()
