from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.config import settings
from app.db.models import Base

# ── Engine configuration ─────────────────────────────────────────────────────
# PostgreSQL: connection pooling with configurable pool size.
# SQLite:     pooling is not supported (NullPool used automatically by SQLAlchemy).

_engine_kwargs = {
    "echo": False,
    "future": True,
}

if not settings.is_sqlite:
    # PostgreSQL connection pool settings
    _engine_kwargs.update({
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_recycle": settings.db_pool_recycle,
        "pool_pre_ping": True,  # Detect stale connections before use
    })

engine = create_async_engine(settings.database_url, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
