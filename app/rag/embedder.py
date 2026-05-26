"""
Embedder
Wraps Ollama's nomic-embed-text model for local embedding generation.
Used by the RAG ingestion script and retriever tool.
"""
import httpx
from app.config import settings
from app.utils.logger import logger


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Ollama nomic-embed-text.
    Returns a list of float vectors, one per input text.
    Raises RuntimeError if Ollama is unreachable.
    """
    embeddings: list[list[float]] = []
    url = f"{settings.ollama_base_url}/api/embeddings"

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i, text in enumerate(texts):
            try:
                response = await client.post(
                    url,
                    json={"model": "nomic-embed-text", "prompt": text},
                )
                response.raise_for_status()
                embeddings.append(response.json()["embedding"])
            except httpx.ConnectError:
                raise RuntimeError(
                    f"Cannot connect to Ollama at {settings.ollama_base_url}. "
                    "Make sure Ollama is running: `ollama serve`"
                )
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Ollama embedding request failed (HTTP {exc.response.status_code}): {exc}"
                )
            except KeyError:
                raise RuntimeError(
                    "Ollama response missing 'embedding' key. "
                    "Ensure nomic-embed-text is pulled: `ollama pull nomic-embed-text`"
                )

            if (i + 1) % 10 == 0:
                logger.debug(f"Embedded {i + 1}/{len(texts)} chunks")

    return embeddings


async def embed_single(text: str) -> list[float]:
    """Convenience wrapper for a single text embedding."""
    results = await embed_texts([text])
    return results[0]
