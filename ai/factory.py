"""Фабрика LLM-провайдеров по конфигурации.

Режимы:
  AI_MODE=local  -> LLM_PROVIDER=local (Ollama). БЕЗ облачного fallback.
  AI_MODE=cloud  -> LLM_PROVIDER=openai (OpenAI-совместимый API).

В AI_MODE=local провайдер обращается только к 127.0.0.1/localhost
(защита от утечки текста, см. ai/ollama.py _assert_local_url).
"""
import logging

import config
from ai.base import LLMError, LLMProvider
from ai.ollama import OllamaProvider

logger = logging.getLogger(__name__)


def _is_local_mode(cfg=config) -> bool:
    return getattr(cfg, "AI_MODE", "local").lower() == "local"


def create_llm_provider(cfg=config) -> LLMProvider:
    """Создаёт провайдера по конфигурации.

    - AI_MODE=local: всегда Ollama (локальная модель из OLLAMA_MODEL).
    - AI_MODE=cloud: openai / openai-compatible.
    """
    if _is_local_mode(cfg):
        return OllamaProvider(
            base_url=getattr(cfg, "OLLAMA_BASE_URL", ""),
            model=getattr(cfg, "OLLAMA_MODEL", "qwen3:8b"),
            timeout=getattr(cfg, "LLM_TIMEOUT", 120),
            retries=getattr(cfg, "LLM_RETRIES", 2),
        )

    provider_name = getattr(cfg, "LLM_PROVIDER", "openai").lower()
    if provider_name in ("openai", "openai-compatible", "openrouter", "yandexgpt"):
        if not getattr(cfg, "LLM_API_KEY", ""):
            logger.warning("LLM_API_KEY не задан — LLM-провайдер создан, но вернёт None/пустоту")
        from ai.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            api_key=getattr(cfg, "LLM_API_KEY", ""),
            base_url=getattr(cfg, "LLM_BASE_URL", "https://api.openai.com/v1"),
            model=getattr(cfg, "LLM_MODEL", "gpt-4o-mini"),
            timeout=getattr(cfg, "LLM_TIMEOUT", 60),
            retries=getattr(cfg, "LLM_RETRIES", 2),
            auth_type=getattr(cfg, "LLM_AUTH_TYPE", "auto"),
            auth_header=getattr(cfg, "LLM_AUTH_HEADER", "Authorization"),
        )
    raise LLMError(
        f"Неизвестный LLM_PROVIDER: {provider_name}",
        "Поддерживаются: local (Ollama), openai (OpenAI-совместимый API)",
    )


__all__ = ["create_llm_provider", "_is_local_mode"]