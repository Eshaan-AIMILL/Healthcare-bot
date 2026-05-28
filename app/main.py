import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.portals import router as portals_router
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
app.include_router(portals_router)
app.include_router(alerts_router)

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
