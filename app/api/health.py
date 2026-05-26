from fastapi import APIRouter
import httpx
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "status": "ok",
        "ollama_connected": ollama_ok,
        "model": settings.ollama_model,
    }


@router.get("/v1/models")
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": "healthcare-bot",
                "object": "model",
                "created": 1700000000,
                "owned_by": "local",
            }
        ],
    }
