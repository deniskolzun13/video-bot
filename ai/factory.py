"""Фабрика LLM-провайдеров по конфигурации."""
import logging

import config
from ai.base import LLMError, LLMProvider
from ai.openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)


def create_llm_provider(cfg=config) -> LLMProvider:
    """Создаёт провайдера по config.LLM_PROVIDER.

    Поддерживаемые значения:
      - openai / openai-compatible (по умолчанию): любой OpenAI-совместимый API.
    """
    provider_name = getattr(cfg, "LLM_PROVIDER", "openai").lower()
    if provider_name in ("openai", "openai-compatible", "openrouter", "yandexgpt"):
        if not getattr(cfg, "LLM_API_KEY", ""):
            logger.warning("LLM_API_KEY не задан — LLM-провайдер создан, но вернёт None/пустоту")
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
        "Поддерживаются: openai (OpenAI-совместимый API)",
    )