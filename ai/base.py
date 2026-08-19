"""Интерфейс LLM-провайдера."""
from abc import ABC, abstractmethod


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


class LLMError(Exception):
    """Ошибка LLM — понятное сообщение + технические детали."""
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details