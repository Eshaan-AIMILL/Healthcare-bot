from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b-instruct-q8_0"
    database_url: str = "sqlite+aiosqlite:///./data/healthcare.db"
    chroma_persist_dir: str = "./data/chroma"
    log_level: str = "INFO"
    seed_rows_per_table: int = 1500
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120


settings = Settings()
