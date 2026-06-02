from fastapi import FastAPI
from app.services.common import AgentStatePayload, payload_to_state, state_to_payload_dict
from app.agents.billing import billing_node
from app.utils.logger import setup_logger

setup_logger()
app = FastAPI(title="Billing Agent Microservice", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "billing"}

@app.post("/chat/billing")
async def chat_billing(payload: AgentStatePayload):
    state = payload_to_state(payload)
    result_state = await billing_node(state)
    return state_to_payload_dict(result_state)
