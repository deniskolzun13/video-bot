"""Механизм отмены задачи (CancellationToken).

Позволяет асинхронно остановить обработку job: pipeline проверяет токен
после каждого этапа и поднимает CancellationError; ffmpeg (субпроцесс)
получает Process handle и завершается через terminate()/kill().
"""
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CancellationError(Exception):
    """Задача отменена пользователем/системой."""


@dataclass
class CancellationToken:
    _cancelled: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _reason: str = field(default="", init=False)

    def cancel(self, reason: str = "Отменено пользователем") -> None:
        """Запрашивает отмену. Потокобезопасно, идемпотентно."""
        with self._lock:
            if not self._cancelled:
                self._cancelled = True
                self._reason = reason
                logger.info("Отмена запрошена: %s", reason)

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason or "Отменено пользователем"

    def check(self) -> None:
        """Поднимает CancellationError, если отмена запрошена."""
        if self.is_cancelled:
            raise CancellationError(self.reason)

    async def check_async(self) -> None:
        """Асинхронная проверка (просто обёртка над check)."""
        self.check()


def cancelled_by(token: CancellationToken | None) -> bool:
    """Удобный предикат: True, если отмена запрошена (None — никогда)."""
    return bool(token) and token.is_cancelled