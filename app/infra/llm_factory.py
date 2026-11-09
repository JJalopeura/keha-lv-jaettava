from __future__ import annotations

from .azure_client import AzureOpenAIClient
from .config import Settings


class LLMUnavailableError(RuntimeError):
    """Raised when the configured Azure AI Foundry backend cannot be used."""


def create_llm_client(
    settings: Settings,
    *,
    purpose: str = "core",
    model: str | None = None,
    api_key: str | None = None,
    host: str | None = None,
    timeout_s: float | None = None,
) -> AzureOpenAIClient:
    """Build the Azure AI Foundry client for `core` (JSON work) or `chat` (prose).

    This project has no offline fallback. If a host, deployment name or key is
    missing the call fails loudly instead of silently degrading to a mock.
    """
    host_key = "chat" if purpose == "chat" else "core"
    target_host = host or getattr(settings, f"azure_openai_host_{host_key}")
    target_model = model or getattr(settings, f"azure_openai_model_{host_key}")
    target_key = api_key or getattr(settings, f"azure_openai_api_key_{host_key}")

    missing = [
        name
        for name, value in (
            (f"AZURE_OPENAI_HOST_{host_key.upper()}", target_host),
            (f"AZURE_OPENAI_MODEL_{host_key.upper()}", target_model),
            (f"AZURE_OPENAI_API_KEY_{host_key.upper()}", target_key),
        )
        if not str(value or "").strip()
    ]
    if missing:
        raise LLMUnavailableError(
            "Azure AI Foundry is not configured. Missing: " + ", ".join(missing)
        )

    return AzureOpenAIClient(
        host=target_host,
        api_version=settings.azure_openai_api_version,
        api_key=target_key,
        model=target_model,
        timeout_s=timeout_s
        if timeout_s is not None
        else getattr(settings, f"azure_openai_timeout_s_{host_key}"),
    )
