from fastapi import FastAPI
from app.services.common import AgentStatePayload, payload_to_state, state_to_payload_dict
from app.agents.patient import patient_node
from app.utils.logger import setup_logger

setup_logger()
app = FastAPI(title="Patient Agent Microservice", version="1.0.0")

@app.get("/health")
def health():
    return {"status": "ok", "service": "patient"}

@app.post("/chat/patient")
async def chat_patient(payload: AgentStatePayload):
    state = payload_to_state(payload)
    result_state = await patient_node(state)
    return state_to_payload_dict(result_state)
