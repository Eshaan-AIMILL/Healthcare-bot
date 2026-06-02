import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.alerts import router as alerts_router
from app.api.portals import router as portals_router
from app.db.session import init_db
from app.utils.logger import setup_logger
from app.alerts.scheduler import alert_scheduler_loop
from app.config import settings

# ── Rate Limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])


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

# ── Rate Limiting Middleware ──────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS Middleware ───────────────────────────────────────────────────────────
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
app.include_router(portals_router)

# Serve dashboard as static site at /dashboard
dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.exists(dashboard_path):
    chat_html_path = os.path.join(dashboard_path, "chat.html")

    @app.get("/", include_in_schema=False)
    async def serve_chat_ui():
        return FileResponse(chat_html_path, media_type="text/html")

    app.mount(
        "/dashboard",
        StaticFiles(directory=dashboard_path, html=True),
        name="dashboard",
    )
