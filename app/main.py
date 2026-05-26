from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.portals import router as portals_router
from app.db.session import init_db
from app.utils.logger import setup_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logger()
    await init_db()
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
app.include_router(portals_router)

# Serve the dashboard as a static file at /dashboard
dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.exists(dashboard_path):
    app.mount("/dashboard", StaticFiles(directory=dashboard_path, html=True), name="dashboard")