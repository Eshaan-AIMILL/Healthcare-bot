import os

# Suppress ChromaDB telemetry warnings at the environment level
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")

import chromadb
from chromadb.config import Settings as ChromaSettings
from app.config import settings

try:
    from chromadb.telemetry.product.posthog import Posthog

    def _disable_posthog_capture(self, *args, **kwargs):
        return None

    Posthog.capture = _disable_posthog_capture
    Posthog._direct_capture = _disable_posthog_capture
except Exception:
    pass


def get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=settings.chroma_persist_dir,
        settings=ChromaSettings(
            anonymized_telemetry=False,
            allow_reset=True,
        ),
    )


def get_or_create_collection(
    client: chromadb.ClientAPI,
    name: str,
) -> chromadb.Collection:
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )
