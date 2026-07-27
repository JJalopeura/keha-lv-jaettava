from __future__ import annotations

from typing import Protocol

from .azure_client import AzureOpenAIClient
from .config import Settings
from .mock_ingest import MockIngestBackend


class IngestBackend(Protocol):
    def create_vector_store(self, name: str) -> str:
        ...

    def upload_file_from_path(self, path: str) -> tuple[str, str]:
        ...

    def attach_file_to_vector_store(self, vector_store_id: str, file_id: str) -> str:
        ...

    def wait_vector_store_file_completed(self, vector_store_id: str, vs_file_id: str) -> None:
        ...


def create_ingest_backend(settings: Settings, *, purpose: str = "core") -> IngestBackend:
    provider = (settings.llm_provider or "mock").strip().lower()
    has_key = bool(settings.azure_openai_api_key_chat if purpose == "chat" else settings.azure_openai_api_key_core)
    if provider == "mock" or (settings.demo_mode and not has_key):
        return MockIngestBackend()
    if provider != "azure":
        raise RuntimeError(f"Unsupported ingest provider: {provider}")
    if not has_key:
        raise RuntimeError("Azure OpenAI is selected but its API key is missing.")

    if purpose == "chat":
        return AzureOpenAIClient(
            host=settings.azure_openai_host_chat,
            api_version=settings.azure_openai_api_version,
            api_key=settings.azure_openai_api_key_chat,
            model=settings.azure_openai_model_chat,
            timeout_s=settings.azure_openai_timeout_s_chat,
        )

    return AzureOpenAIClient(
        host=settings.azure_openai_host_core,
        api_version=settings.azure_openai_api_version,
        api_key=settings.azure_openai_api_key_core,
        model=settings.azure_openai_model_core,
        timeout_s=settings.azure_openai_timeout_s_core,
    )
