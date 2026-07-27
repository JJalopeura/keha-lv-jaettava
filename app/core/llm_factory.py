from __future__ import annotations

from typing import Any, Iterator, Protocol

import os

from .azure_client import AzureOpenAIClient
from .config import Settings
from .mock_llm import MockLLMClient


class LLMClient(Protocol):
    def responses(
        self,
        *,
        input_text: str,
        vector_store_ids: list[str] | None,
        response_format: str | None,
        max_output_tokens: int | None,
        reasoning_effort: str | None,
        text_verbosity: str | None,
    ) -> dict:
        ...

    def responses_stream(
        self,
        *,
        input_text: str,
        vector_store_ids: list[str] | None,
        response_format: str | None,
        max_output_tokens: int | None,
        previous_response_id: str | None,
        reasoning_effort: str | None,
        text_verbosity: str | None,
    ) -> Iterator[dict[str, Any]]:
        ...

    def extract_output_text(self, resp: dict) -> str | None:
        ...


def create_llm_client(
    settings: Settings,
    *,
    purpose: str = "core",
    model: str | None = None,
    api_key: str | None = None,
    host: str | None = None,
) -> LLMClient:
    provider = (settings.llm_provider or "mock").strip().lower()
    force_mock = str(os.getenv("KEHA_LV_FORCE_MOCK_LLM", "")).strip().lower() in {"1", "true", "yes", "on"}
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

    if purpose == "chat":
        return AzureOpenAIClient(
            host=(host or settings.azure_openai_host_chat),
            api_version=settings.azure_openai_api_version,
            api_key=api_key or settings.azure_openai_api_key_chat,
            model=model or settings.azure_openai_model_chat,
            timeout_s=settings.azure_openai_timeout_s_chat,
        )

    return AzureOpenAIClient(
        host=(host or settings.azure_openai_host_core),
        api_version=settings.azure_openai_api_version,
        api_key=api_key or settings.azure_openai_api_key_core,
        model=model or settings.azure_openai_model_core,
        timeout_s=settings.azure_openai_timeout_s_core,
    )
