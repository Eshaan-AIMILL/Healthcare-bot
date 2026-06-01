import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set ChromaDB telemetry flags at import time via os.environ
# These are NOT Pydantic settings — ChromaDB reads them directly
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model:    str = "mistral-nemo"
    ollama_text2sql_model: str = "qwen2.5:7b"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url:       str = "sqlite+aiosqlite:///./data/healthcare.db"
    chroma_persist_dir: str = "./data/chroma"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Seeder ────────────────────────────────────────────────────────────────
    seed_rows_per_table: int = 2000

    # ── RAG ───────────────────────────────────────────────────────────────────
    rag_chunk_size:    int = 800
    rag_chunk_overlap: int = 120

    # ── SMTP / Email Alerts ───────────────────────────────────────────────────
    smtp_host:     str  = "smtp.gmail.com"
    smtp_port:     int  = 587
    smtp_user:     str  = ""
    smtp_password: str  = ""
    smtp_from:     str  = "healthbot-alerts@hospital.local"
    smtp_use_tls:  bool = True

    # ── Alert Scheduler ───────────────────────────────────────────────────────
    alert_emails_enabled:         bool = False
    alert_check_interval_minutes: int  = 60

    # ── Open WebUI / Security ──────────────────────────────────────────────────
    openwebui_secret_key: str = "super-secret-enterprise-key-change-in-prod"

settings = Settings()