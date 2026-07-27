from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import load_settings
from app.core.db import DB
from app.domains.lupademo.routes import create_lupademo_router


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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "llm_provider": settings.llm_provider}
