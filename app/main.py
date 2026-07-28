from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import load_settings
from app.core.db import DB
from app.domains.lupademo.routes import create_lupademo_router

logger = logging.getLogger(__name__)

settings = load_settings()
Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
db = DB(settings.db_path)

app = FastAPI(title="KEHA LV AI-demo")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "domains" / "lupademo" / "static")),
    name="static",
)
app.include_router(create_lupademo_router(lambda: db, lambda: settings))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "llm_provider": settings.llm_provider}
