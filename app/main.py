import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.alerts import router as alerts_router
from app.db.session import init_db
from app.utils.logger import setup_logger
from app.alerts.scheduler import alert_scheduler_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    await init_db()
    # Start background alert scheduler
    asyncio.create_task(alert_scheduler_loop())
    yield


app = FastAPI(
    title="Healthcare Bot API",
    version="2.0.0",
    description="Multi-agent healthcare operations AI — local-first, OpenAI-compatible",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(health_router)
app.include_router(alerts_router)

