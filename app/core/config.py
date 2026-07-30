from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _bool_env(name: str, default: bool = False) -> bool:
    """Parse an environment variable as a boolean. Accepts 1/true/yes/on (case-insensitive)."""
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"} or default


@dataclass(frozen=True)
class Settings:
    db_path: str
    uploads_dir: str
    llm_provider: str
    demo_mode: bool = True
    force_mock_llm: bool = False
    azure_openai_host_core: str = ""
    azure_openai_host_chat: str = ""
    azure_openai_api_key_core: str = ""
    azure_openai_api_key_chat: str = ""
    azure_openai_api_version: str = "2025-04-01-preview"
    azure_openai_model_core: str = ""
    azure_openai_model_chat: str = ""
    azure_openai_timeout_s_core: float = 60.0
    azure_openai_timeout_s_chat: float = 60.0
    company_vector_store_ids: list[str] = field(default_factory=list)


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
        llm_provider=os.getenv("KEHA_LV_LLM_PROVIDER", "mock").strip().lower() or "mock",
        demo_mode=_bool_env("KEHA_LV_DEMO_MODE", default=True),
        force_mock_llm=_bool_env("KEHA_LV_FORCE_MOCK_LLM", default=False),
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
