"""Локальный LLM-провайдер через Ollama (AI_MODE=local).

Интерфейс (LLMProvider):
  health_check()   — проверяет доступность Ollama и наличие модели.
  complete()       — синхронный ответ на промпт (без stream).
  generate_json()  — ответ с извлечением JSON из ответа.
  stream()         — потоковый ответ (для будущих use cases).

Требования безопасности (ТЗ v2.0.1+, раздел 40):
  - В AI_MODE=local провайдер может обращаться ТОЛЬКО к 127.0.0.1/localhost.
  - Если OLLAMA_BASE_URL указывает на внешний хост — ConfigurationError.
  - НИКАКОГО облачного fallback: при недоступности Ollama — ошибка.

Модель не хардкодится — берётся из config.OLLAMA_MODEL (например qwen3:8b).
"""
import asyncio
import json
import logging
from urllib.parse import urlparse

import httpx

import config
from ai.base import LLMError, LLMProvider
from utils.errors import ConfigurationError
from utils.json_utils import extract_json

logger = logging.getLogger(__name__)


def _assert_local_url(url: str) -> str:
    """Защита от утечки текста: разрешён только 127.0.0.1 / localhost."""
    if not url:
        raise ConfigurationError(
            "OLLAMA_BASE_URL не задан", "Необходимо указать URL локального Ollama"
        )
    host = (urlparse(url).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ConfigurationError(
            "AI_MODE=local запрещает внешние LLM-эндпоинты",
            f"OLLAMA_BASE_URL={url} указывает на внешний хост ({host}). "
            "Используйте http://127.0.0.1:11434",
        )
    return url.rstrip("/")


class OllamaProvider(LLMProvider):
    """Провайдер локальной LLM через HTTP API Ollama."""

    def __init__(
        self,
        base_url: str = "",
        model: str = "",
        timeout: float = 0,
        retries: int = 0,
    ):
        self.base_url = _assert_local_url(base_url or config.OLLAMA_BASE_URL)
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or float(getattr(config, "LLM_TIMEOUT", 120))
        self.retries = retries or int(getattr(config, "LLM_RETRIES", 2))

    def name(self) -> str:
        return f"ollama ({self.model})"

    # --- health ---
    async def health_check(self) -> dict:
        """Проверяет Ollama и модель. Возвращает {ok, status, message}."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/api/tags")
        except Exception as exc:
            return {
                "ok": False,
                "status": "unreachable",
                "message": (
                    "Local LLM unavailable. Start Ollama and check model configuration. "
                    f"({self.base_url} недоступен: {exc})"
                ),
            }
        if response.status_code != 200:
            return {
                "ok": False,
                "status": "error",
                "message": f"Ollama вернул HTTP {response.status_code}",
            }
        try:
            models = response.json().get("models", [])
        except Exception:
            models = []
        names = {m.get("name", "") for m in models}
        if self.model not in names:
            return {
                "ok": False,
                "status": "model_missing",
                "message": f"Model {self.model} is not installed. Run: ollama pull {self.model}",
            }
        return {
            "ok": True,
            "status": "ready",
            "message": f"Ollama ready: {self.model}",
        }

    # --- helpers ---
    async def _request(self, payload: dict) -> dict:
        """POST /api/generate. Возвращает JSON ответа. Без ретраев здесь —
        ретраи на уровне complete() для устойчивости."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
        if response.status_code != 200:
            raise LLMError(
                f"Local LLM error: Ollama вернул HTTP {response.status_code}",
                response.text[:300],
            )
        return response.json()

    async def complete(self, prompt: str) -> str:
        """Отправляет промпт, возвращает текст ответа. Поднимает LLMError."""
        if not prompt.strip():
            return ""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        last_exc: Exception | None = None
        for attempt in range(max(1, self.retries + 1)):
            try:
                data = await self._request(payload)
                content = (data.get("response") or "").strip()
                if content:
                    return content
                return ""
            except LLMError as exc:
                last_exc = exc
                if "unreachable" in str(exc).lower():
                    break
                await asyncio.sleep(1.0 * (attempt + 1))
        raise LLMError(
            "Local LLM unavailable. Start Ollama and check model configuration.",
            str(last_exc) if last_exc else "",
        )

    async def generate_json(self, prompt: str) -> dict | None:
        """Возвращает извлечённый JSON-объект из ответа (или None)."""
        content = await self.complete(prompt)
        if not content:
            return None
        data = extract_json(content)
        return data if isinstance(data, dict) else None

    async def stream(self, prompt: str):
        """Потоковый ответ (generator строк). Поднимает LLMError при ошибке."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": 0.2},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                    if response.status_code != 200:
                        raise LLMError(
                            f"Local LLM error: Ollama вернул HTTP {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        piece = obj.get("response") or ""
                        if piece:
                            yield piece
                        if obj.get("done"):
                            break
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(
                "Local LLM unavailable. Start Ollama and check model configuration.",
                str(exc),
            ) from exc


async def assert_local_llm_ok(provider: OllamaProvider | None = None) -> None:
    """Проверка на старте бота в AI_MODE=local.

    Поднимает ConfigurationError с понятным сообщением, если Ollama/модель
    недоступны. НЕ переключается на облако.
    """
    provider = provider or OllamaProvider()
    check = await provider.health_check()
    if not check["ok"]:
        raise ConfigurationError(
            check["message"],
            f"OLLAMA_BASE_URL={provider.base_url}, model={provider.model}",
        )


__all__ = ["OllamaProvider", "assert_local_llm_ok", "_assert_local_url"]