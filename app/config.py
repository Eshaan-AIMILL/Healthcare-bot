import os
import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set ChromaDB telemetry flags at import time via os.environ
# These are NOT Pydantic settings — ChromaDB reads them directly
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "False")


def _generate_default_secret() -> str:
    """Generate a random secret key for development. Production MUST override."""
    return secrets.token_urlsafe(32)


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
    database_url:       str = "postgresql+asyncpg://healthcare:healthcare_pass@localhost:5432/healthcare"
    chroma_persist_dir: str = "./data/chroma"

    # Connection pooling (ignored for SQLite)
    db_pool_size:     int = 10
    db_max_overflow:  int = 20
    db_pool_recycle:  int = 3600  # seconds — recycle connections every hour

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

    # ── Open WebUI / Security ─────────────────────────────────────────────────
    openwebui_secret_key: str = "super-secret-enterprise-key-change-in-prod"  # Production MUST override in .env

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "*"  # Comma-separated origins, e.g. "https://app.example.com,http://localhost:3000"

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_chat: str = "10/minute"  # Requests per period for chat endpoint
    rate_limit_default: str = "60/minute"  # Default rate limit for all endpoints

    # ── Gunicorn / Workers ────────────────────────────────────────────────────
    gunicorn_workers: int = 4

    # ── Microservices URLs ────────────────────────────────────────────────────
    billing_service_url:    str = "http://localhost:8002"
    compliance_service_url: str = "http://localhost:8003"
    pharmacy_service_url:   str = "http://localhost:8004"
    patient_service_url:    str = "http://localhost:8005"
    dispatch_service_url:   str = "http://localhost:8006"
    cache_service_url:      str = "http://localhost:8007"

    # ── Cache Service ─────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_billing: int = 600        # 10 minutes
    cache_ttl_compliance: int = 900     # 15 minutes
    cache_ttl_pharmacy: int = 300       # 5 minutes
    cache_ttl_patient: int = 600        # 10 minutes
    cache_ttl_dispatch: int = 180       # 3 minutes
    cache_ttl_cross_domain: int = 300   # 5 minutes
    cache_ttl_general: int = 1800       # 30 minutes


    @property
    def is_sqlite(self) -> bool:
        """Check if the configured database is SQLite."""
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def get_secret_key(self) -> str:
        """Return the secret key, auto-generating one if not set."""
        if self.openwebui_secret_key:
            return self.openwebui_secret_key
        if not hasattr(self, "_generated_secret_key"):
            self._generated_secret_key = _generate_default_secret()
        return self._generated_secret_key


settings = Settings()

# ── Startup Warnings ─────────────────────────────────────────────────────────
import sys

if not settings.openwebui_secret_key:
    print(
        "⚠️  WARNING: OPENWEBUI_SECRET_KEY is not set. "
        "A random key will be generated each startup. "
        "Set OPENWEBUI_SECRET_KEY in .env for production.",
        file=sys.stderr,
    )

if settings.cors_origins == "*":
    print(
        "⚠️  WARNING: CORS is set to allow ALL origins (*). "
        "Set CORS_ORIGINS to specific domains in production.",
        file=sys.stderr,
    )