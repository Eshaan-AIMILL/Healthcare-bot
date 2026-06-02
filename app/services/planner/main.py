import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.services.planner.chat import router as chat_router
from app.api.health import router as health_router
from app.api.alerts import router as alerts_router
from app.db.session import init_db
from app.utils.logger import setup_logger
from app.alerts.scheduler import alert_scheduler_loop
from app.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    await init_db()
    # Start background alert scheduler inside Gateway
    asyncio.create_task(alert_scheduler_loop())
    yield

app = FastAPI(
    title="Healthcare Bot Gateway API",
    version="2.0.0",
    description="Microservices API Gateway & Intent Router",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(health_router)
app.include_router(alerts_router)
