"""Интерфейс LLM-провайдера."""
from abc import ABC, abstractmethod

from utils.errors import ProviderError


class LLMProvider(ABC):
    """Единый интерфейс для LLM. Позволяет подключать любые OpenAI-совместимые API,
    а в будущем — Gemini, Claude и др. (через новые реализации)."""

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Отправляет одиночный user-промпт и возвращает текст ответа.
        Поднимает LLMError при ошибке API (после ретраев)."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Человекочитаемое имя провайдера (для логов)."""
        ...


class LLMError(ProviderError):
    """Ошибка LLM — понятное сообщение + технические детали (наследует ProviderError)."""