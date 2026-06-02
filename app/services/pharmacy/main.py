from fastapi import FastAPI
from app.services.common import AgentStatePayload, payload_to_state, state_to_payload_dict
from app.agents.pharmacy import pharmacy_node
from app.utils.logger import setup_logger

setup_logger()
app = FastAPI(title="Pharmacy Agent Microservice", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "pharmacy"}

@app.post("/chat/pharmacy")
async def chat_pharmacy(payload: AgentStatePayload):
    state = payload_to_state(payload)
    result_state = await pharmacy_node(state)
    return state_to_payload_dict(result_state)
