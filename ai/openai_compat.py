"""OpenAI-совместимый LLM-провайдер (OpenAI, OpenRouter, YandexGPT и т.п.)."""
import logging

import httpx

from ai.base import LLMError, LLMProvider
from utils.retry import RetryableError, retry_async

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """Реализация для любого OpenAI-compatible /chat/completions эндпоинта."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: float = 60,
        retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = retries

    def name(self) -> str:
        return f"openai-compat ({self.model})"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise LLMError("LLM_API_KEY не настроен", "Нет API-ключа для LLM")
        # YandexGPT/OpenRouter используют Api-Key, OpenAI — Bearer; определяем по префиксу
        if self.api_key.startswith(("sk-", "t1.")):
            return {"Authorization": f"Bearer {self.api_key}"}
        return {"Authorization": f"Api-Key {self.api_key}"}

    async def _request(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                    },
                )
        except httpx.HTTPStatusError as exc:
            raise RetryableError(
                f"LLM HTTP {exc.response.status_code}", exc.response.status_code
            ) if exc.response.status_code in (429, 500, 502, 503, 504) else LLMError(
                "Ошибка LLM-API", str(exc)
            )
        except httpx.TransportError as exc:
            raise RetryableError(f"LLM transport error: {exc}")
        except Exception as exc:
            raise LLMError("Ошибка LLM-API", str(exc))

        if response.status_code != 200:
            raise RetryableError(f"LLM status {response.status_code}", response.status_code)
        try:
            return response.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise LLMError("LLM вернул неожиданный ответ", str(exc))

    async def complete(self, prompt: str) -> str:
        if not self.api_key:
            return ""
        return await retry_async(self._request, prompt, retries=self.retries)