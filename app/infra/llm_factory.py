from __future__ import annotations

import os
from typing import Any

from .azure_client import AzureOpenAIClient
from .config import Settings
from .mock_llm import MockLLMClient


def create_llm_client(
    settings: Settings,
    *,
    purpose: str = "core",
    model: str | None = None,
    api_key: str | None = None,
    host: str | None = None,
    force_mock: bool = False,
    db: Any = None,
    case_id: str | None = None,
) -> AzureOpenAIClient | MockLLMClient:
    provider = (settings.llm_provider or "mock").strip().lower()
    force_mock = force_mock or str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() in {"1", "true", "yes", "on"}
    # Presenter-mode cases always use mock regardless of provider config.
    if not force_mock and db is not None and case_id is not None:
        try:
            artifact = db.get_artifact(case_id, "lv_project", "main")
            if artifact and artifact.get("data_json", {}).get("presenter_mode"):
                force_mock = True
        except Exception:
            pass
    if purpose == "chat":
        has_key = bool(api_key or settings.azure_openai_api_key_chat)
    else:
        has_key = bool(api_key or settings.azure_openai_api_key_core)
    if provider == "mock" or force_mock or (settings.demo_mode and not has_key):
        return MockLLMClient()
    if provider != "azure":
        raise RuntimeError(f"Unsupported LLM provider: {provider}")
    if not has_key:
        raise RuntimeError("Azure OpenAI is selected but its API key is missing.")

    host_key = "chat" if purpose == "chat" else "core"
    conf = {
        "host": host or getattr(settings, f"azure_openai_host_{host_key}"),
        "api_version": settings.azure_openai_api_version,
        "api_key": api_key or getattr(settings, f"azure_openai_api_key_{host_key}"),
        "model": model or getattr(settings, f"azure_openai_model_{host_key}"),
        "timeout_s": getattr(settings, f"azure_openai_timeout_s_{host_key}"),
    }
    return AzureOpenAIClient(**conf)
