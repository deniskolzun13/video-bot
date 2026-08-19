"""Retry с экспоненциальным backoff для внешних API.

Повторяет корутину до retries раз (по умолчанию 3):
паузы 1с -> 2с -> 4с. Не делает бесконечные retry.
"""
import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

T = TypeVar("T")


class RetryableError(Exception):
    """Ошибка, которую можно повторить (rate limit / временный сбой)."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args,
    retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = (RetryableError,),
    **kwargs,
) -> T:
    """Вызывает await func(*args, **kwargs), повторяя при RetryableError.

    Exponential backoff: 1s, 2s, 4s... но не больше max_delay и не дольше
    max(retries) попыток. Последнее исключение пробрасывается наверх.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries):
        try:
            return await func(*args, **kwargs)
        except retry_on as exc:
            last_exc = exc
            if attempt == retries - 1:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "Повтор через %.1fс (попытка %d/%d): %s",
                delay, attempt + 1, retries, exc,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def is_retryable_status(status_code: int) -> bool:
    return status_code in RETRYABLE_STATUS