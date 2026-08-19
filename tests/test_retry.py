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
        assert is_retryable_status(503)
        assert not is_retryable_status(200)
        assert not is_retryable_status(404)