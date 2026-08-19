"""Unit tests для retry-механизма (без реальных API)."""
import asyncio

import pytest

from utils.retry import RetryableError, retry_async


class TestRetry:
    def test_success_first_try(self):
        calls = []

        async def ok():
            calls.append(1)
            return "ok"

        result = asyncio.run(retry_async(ok))
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RetryableError("temp")
            return "done"

        result = asyncio.run(retry_async(flaky, retries=3, base_delay=0.01))
        assert result == "done"
        assert len(calls) == 3

    def test_gives_up_after_max_retries(self):
        calls = []

        async def always_fails():
            calls.append(1)
            raise RetryableError("always")

        with pytest.raises(RetryableError):
            asyncio.run(retry_async(always_fails, retries=3, base_delay=0.01))
        assert len(calls) == 3

    def test_non_retryable_exception_propagates(self):
        async def fails():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            asyncio.run(retry_async(fails, retries=3))

    def test_retryable_status_codes(self):
        from utils.retry import is_retryable_status
        assert is_retryable_status(429)
        assert is_retryable_status(500)
        assert is_retryable_status(502)
        assert is_retryable_status(503)
        assert is_retryable_status(504)
        assert is_retryable_status(408)  # timeout — тоже retry
        assert not is_retryable_status(200)
        assert not is_retryable_status(400)
        assert not is_retryable_status(401)
        assert not is_retryable_status(404)

    def test_retry_408_retries(self):
        calls = []

        async def timeout_once():
            calls.append(1)
            if len(calls) == 1:
                raise RetryableError("timeout", 408)
            return "ok"

        result = asyncio.run(retry_async(timeout_once, retries=3, base_delay=0.01))
        assert result == "ok"
        assert len(calls) == 2

    def test_non_retryable_status_no_retry(self, monkeypatch):
        """400/401/404 не ретраятся — провайдер поднимает LLMError сразу."""
        import httpx
        from ai.openai_compat import OpenAICompatProvider
        from ai.base import LLMError

        class FakeResponse:
            status_code = 400
            text = "bad request"

        class FakeTransportError(httpx.TransportError):
            pass

        provider = OpenAICompatProvider(api_key="sk-test", retries=3, base_url="http://x")

        async def fake_post(*args, **kwargs):
            raise httpx.HTTPStatusError(
                "400", request=httpx.Request("POST", "http://x"), response=FakeResponse()
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        async def run():
            try:
                await provider.complete("prompt")
            except LLMError:
                return "raised"
            return "no error"

        result = asyncio.run(run())
        assert result == "raised"

    def test_retryable_status_retries(self, monkeypatch):
        """503 — ретраится: провайдер пробует несколько раз, потом успех."""
        import httpx
        from ai.openai_compat import OpenAICompatProvider

        class FakeResponse:
            status_code = 503
            text = "unavailable"

        calls = {"n": 0}

        async def fake_post(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.HTTPStatusError(
                    "503", request=httpx.Request("POST", "http://x"), response=FakeResponse()
                )
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "  ok  "}}]
            })

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        provider = OpenAICompatProvider(api_key="sk-test", retries=3, base_url="http://x")

        async def run():
            return await provider.complete("prompt")

        # отключим реальные паузы
        import utils.retry as retry_mod

        async def no_sleep(seconds):
            return None

        monkeypatch.setattr(retry_mod.asyncio, "sleep", no_sleep)

        result = asyncio.run(run())
        assert result == "ok"
        assert calls["n"] == 3

    def test_backoff_grows(self, monkeypatch):
        """Паузы растут: 1с -> 2с -> 4с (экспоненциальный backoff)."""
        from utils.retry import retry_async
        import asyncio as asyncio_mod
        delays = []

        async def spy_sleep(seconds):
            delays.append(seconds)

        monkeypatch.setattr(asyncio_mod, "sleep", spy_sleep)

        async def always_fails():
            raise RetryableError("always")

        with pytest.raises(RetryableError):
            asyncio.run(retry_async(
                always_fails, retries=4, base_delay=1.0, max_delay=8.0
            ))
        assert delays == [1.0, 2.0, 4.0]


class TestAuthHeaders:
    """P1: LLM_AUTH_TYPE=auto не зависит от префикса ключа."""

    def _headers(self, auth_type, key):
        from ai.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(api_key=key, auth_type=auth_type)
        return provider._headers()

    def test_auto_uses_bearer_even_for_non_sk_key(self):
        """auto всегда Bearer — даже для ключа без префикса sk- (YandexGPT)."""
        h = self._headers("auto", "AQVN-xyz")
        assert h == {"Authorization": "Bearer AQVN-xyz"}

    def test_auto_uses_bearer_for_sk_key(self):
        h = self._headers("auto", "sk-abc")
        assert h == {"Authorization": "Bearer sk-abc"}

    def test_bearer_explicit(self):
        h = self._headers("bearer", "sk-abc")
        assert h == {"Authorization": "Bearer sk-abc"}

    def test_api_key_explicit(self):
        h = self._headers("api-key", "AQVN-xyz")
        assert h == {"Authorization": "Api-Key AQVN-xyz"}

    def test_raw_explicit(self):
        h = self._headers("raw", "my-token")
        assert h == {"Authorization": "my-token"}

    def test_custom_header_name(self):
        from ai.openai_compat import OpenAICompatProvider
        provider = OpenAICompatProvider(api_key="k", auth_type="bearer", auth_header="X-API-Key")
        assert provider._headers() == {"X-API-Key": "Bearer k"}

    def test_unknown_auth_type_raises(self):
        from ai.openai_compat import OpenAICompatProvider
        from ai.base import LLMError
        import pytest as _pytest

        provider = OpenAICompatProvider(api_key="k", auth_type="bogus")
        with _pytest.raises(LLMError):
            provider._headers()