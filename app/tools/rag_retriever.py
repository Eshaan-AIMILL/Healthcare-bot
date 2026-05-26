import httpx
from typing import Any
from app.config import settings
from app.rag.chroma_client import get_chroma_client, get_or_create_collection
from app.utils.logger import logger


# Collection names mapped to agent domains
COLLECTION_MAP: dict[str, str] = {
    "billing": "billing_policies",
    "compliance": "compliance_guidelines",
    "patient": "patient_sla_policies",
}


async def embed_query(query: str) -> list[float]:
    url = f"{settings.ollama_base_url}/api/embeddings"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json={"model": "nomic-embed-text", "prompt": query},
            )
            response.raise_for_status()
            return response.json()["embedding"]
    except Exception as exc:
        logger.warning(f"Embedding request failed: {exc}. RAG will return empty context.")
        return []


async def retrieve_context(
    query: str,
    domain: str,
    top_k: int = 5,
) -> list[str]:
    
    collection_name = COLLECTION_MAP.get(domain)
    if not collection_name:
        logger.debug(f"No RAG collection for domain '{domain}'. Skipping retrieval.")
        return []

    embedding = await embed_query(query)
    if not embedding:
        return []

    try:
        client = get_chroma_client()
        collection = get_or_create_collection(client, collection_name)
        results: dict[str, Any] = collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, collection.count()),
            include=["documents", "distances"],
        )
        chunks: list[str] = results.get("documents", [[]])[0]
        distances: list[float] = results.get("distances", [[]])[0]
        logger.info(
            f"RAG retrieved {len(chunks)} chunks for domain='{domain}' "
            f"(distances: {[round(d, 3) for d in distances]})"
        )
        return chunks
    except Exception as exc:
        logger.error(f"RAG retrieval failed for domain='{domain}': {exc}")
        return []
