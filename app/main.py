"""FastAPI entrypoint."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import admin_router, ingest_router
from app.services.engagement_repository import EngagementRepository
from app.runtime import (
    engagement_worker,
    start_core_services,
    stop_core_services,
)
from app.utils.logging import setup_logging


setup_logging()

app = FastAPI(
    title="Yapping Bot API",
    version="0.1.0",
    description="Automation service for crypto yapping content generation and posting.",
)
app.include_router(ingest_router)
app.include_router(admin_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    """Initialise resources."""

    await init_db()
    await EngagementRepository.ensure_schema()
    start_core_services(settings.engagement_auto_start)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Tear down resources."""

    await stop_core_services()


@app.get("/health", tags=["meta"])
async def healthcheck() -> dict[str, str]:
    """Return basic readiness signal."""

    return {"status": "ok", "env": settings.app_env}


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    """Simple landing endpoint."""

    return {"message": "Yapping bot up and running.", "admin_url": "/admin"}
