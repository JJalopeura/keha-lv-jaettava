from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Load .env file if present (before reading os.getenv)
_env_path = ROOT / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


@dataclass(frozen=True)
class Settings:
    db_path: str
    uploads_dir: str
    llm_provider: str
    demo_mode: bool
    azure_openai_host_core: str
    azure_openai_host_chat: str
    azure_openai_api_key_core: str
    azure_openai_api_key_chat: str
    azure_openai_api_version: str
    azure_openai_model_core: str
    azure_openai_model_chat: str
    azure_openai_timeout_s_core: float
    azure_openai_timeout_s_chat: float
    company_vector_store_ids: list[str]
    session_secret: str = "keha-lv-demo-session-secret"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("KEHA_LV_DATA_DIR", str(ROOT / "data")))
    vector_store_ids = [
        item.strip()
        for item in os.getenv("KEHA_LV_COMPANY_VECTOR_STORE_IDS", "").split(",")
        if item.strip()
    ]
    return Settings(
        db_path=os.getenv("KEHA_LV_DB_PATH", str(data_dir / "keha_lv.db")),
        uploads_dir=os.getenv("KEHA_LV_UPLOADS_DIR", str(data_dir / "uploads")),
        session_secret=os.getenv("KEHA_LV_SESSION_SECRET", "keha-lv-demo-session-secret"),
        llm_provider=os.getenv("KEHA_LV_LLM_PROVIDER", "mock").strip().lower() or "mock",
        demo_mode=os.getenv("KEHA_LV_DEMO_MODE", "true").strip().lower() in {"1", "true", "yes", "on"},
        azure_openai_host_core=os.getenv("AZURE_OPENAI_HOST_CORE", ""),
        azure_openai_host_chat=os.getenv("AZURE_OPENAI_HOST_CHAT", ""),
        azure_openai_api_key_core=os.getenv("AZURE_OPENAI_API_KEY_CORE", ""),
        azure_openai_api_key_chat=os.getenv("AZURE_OPENAI_API_KEY_CHAT", ""),
        azure_openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        azure_openai_model_core=os.getenv("AZURE_OPENAI_MODEL_CORE", ""),
        azure_openai_model_chat=os.getenv("AZURE_OPENAI_MODEL_CHAT", ""),
        azure_openai_timeout_s_core=float(os.getenv("AZURE_OPENAI_TIMEOUT_S_CORE", "60")),
        azure_openai_timeout_s_chat=float(os.getenv("AZURE_OPENAI_TIMEOUT_S_CHAT", "60")),
        company_vector_store_ids=vector_store_ids,
    )
