from fastapi import FastAPI
from app.services.common import AgentStatePayload, payload_to_state, state_to_payload_dict
from app.agents.compliance import compliance_node
from app.utils.logger import setup_logger

setup_logger()
app = FastAPI(title="Compliance Agent Microservice", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "compliance"}

@app.post("/chat/compliance")
async def chat_compliance(payload: AgentStatePayload):
    state = payload_to_state(payload)
    result_state = await compliance_node(state)
    return state_to_payload_dict(result_state)
