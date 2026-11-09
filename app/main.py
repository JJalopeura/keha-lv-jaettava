from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.infra.config import load_settings
from app.infra.db import DB
from app.infra.llm_factory import LLMUnavailableError
from app.infra.session import DemoSessionMiddleware
from app.domains.lupademo import services as svc
from app.domains.lupademo.routes import create_lupademo_router
from app.domains.lupademo.services.customer_workspace import ensure_customer_accounts

logger = logging.getLogger(__name__)

settings = load_settings()
Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)
db = DB(settings.db_path)

_session_secret = settings.session_secret
if not _session_secret:
    _session_secret = secrets.token_urlsafe(32)
    logger.warning(
        "KEHA_LV_SESSION_SECRET is unset; generated an ephemeral key. "
        "Browser sessions will not survive a restart."
    )

app = FastAPI(title="KEHA LV — Lupahakemuspalvelu")
app.add_middleware(
    DemoSessionMiddleware,
    secret_key=_session_secret,
    session_cookie="lv_session",
    same_site="lax",
    https_only=False,
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent / "domains" / "lupademo" / "static")),
    name="static",
)
app.include_router(create_lupademo_router(lambda: db, lambda: settings))


def recover_orphaned_jobs(db_obj: DB) -> None:
    """Mark jobs from a previous server process as failed and actionable."""
    for case in db_obj.list_cases():
        case_id = case["id"]
        for permit_type in svc.PERMIT_SCHEMAS:
            for job in svc.list_ai_jobs(db_obj, case_id, permit_type):
                if job.get("status") not in {"pending", "running"}:
                    continue
                try:
                    svc.complete_ai_job(
                        db_obj,
                        case_id,
                        permit_type,
                        job["id"],
                        summary="AI-ajo keskeytyi palvelimen uudelleenkäynnistyksen vuoksi. Käynnistä ajo uudelleen.",
                        failed=True,
                    )
                    logger.warning("Marked orphaned job %s as failed for case %s (%s)", job["id"], case_id, permit_type)
                except Exception:
                    logger.warning("Failed to recover orphaned job %s for case %s", job["id"], case_id, exc_info=True)


@app.on_event("startup")
def startup_recover_orphaned_jobs():
    ensure_customer_accounts(db)
    recover_orphaned_jobs(db)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(LLMUnavailableError)
async def llm_unavailable_handler(request: Request, exc: LLMUnavailableError) -> HTMLResponse | JSONResponse:
    detail = "Azure AI Foundry ei ole käytettävissä. Tarkista yhteysasetukset."
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
    return {
        "status": "ok",
        "llm_provider": "azure",
        "model_core": settings.azure_openai_model_core,
        "model_chat": settings.azure_openai_model_chat,
    }
