from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.infra.config import load_settings
from app.infra.db import DB
from app.infra.llm_factory import LLMUnavailableError
from app.domains.lupademo import services as svc
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


@app.on_event("startup")
def startup_retry_orphaned_jobs():
    """F013: On startup, retry any jobs stuck in 'pending' or 'running' from a previous crash."""
    for case in db.list_cases():
        case_id = case["id"]
        for permit_type in svc.PERMIT_SCHEMAS:
            for job in svc.list_ai_jobs(db, case_id, permit_type):
                if job.get("status") in {"pending", "running"}:
                    try:
                        job = svc.retry_ai_job(db, job["id"])
                        logger.info("Retried orphaned job %s for case %s (%s)", job["id"], case_id, permit_type)
                    except Exception:
                        logger.warning("Failed to retry orphaned job %s for case %s", job["id"], case_id)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(LLMUnavailableError)
async def llm_unavailable_handler(request: Request, exc: LLMUnavailableError) -> HTMLResponse | JSONResponse:
    detail = "Azure AI ei ole käytettävissä. Mock-AI on sallittu vain esittäjätilassa."
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=503, content={"detail": detail})
    return HTMLResponse(
        status_code=503,
        content=(
            "<!doctype html><html lang='fi'><head><meta charset='utf-8'><title>AI-palvelu ei ole käytettävissä</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'></head><body>"
            f"<main><h1>AI-palvelu ei ole käytettävissä</h1><p>{detail}</p></main></body></html>"
        ),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "llm_provider": settings.llm_provider}
