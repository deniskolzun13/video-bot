"""Логирование с контекстом job_id.

Задачи выполняются конкурентно (JOB_CONCURRENCY), поэтому логи разных
заданий перемешиваются. Модуль добавляет contextvar JOB_ID, которая
подставляется в формат логов текущего задания.

Использование:
    from utils.logging import job_context
    with job_context("JOB-123"):
        logger.info("скачал клип")   # -> [job JOB-123] скачал клип
"""
import logging
import logging.config
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

JOB_ID: ContextVar[str] = ContextVar("job_id", default="")


class JobIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        job_id = JOB_ID.get()
        record.job_id = f"[job {job_id}] " if job_id else ""
        return True


class _CtxStore(threading.local):
    def __init__(self) -> None:
        self.ctx = None


@contextmanager
def job_context(job_id: str) -> Iterator[None]:
    """Контекст-менеджер: внутри блока JOB_ID виден всем логгерам."""
    token = JOB_ID.set(job_id or "")
    try:
        yield
    finally:
        JOB_ID.reset(token)


def setup_logging(level: int = logging.INFO) -> None:
    """Настраивает корневой логгер с JobIdFilter (безопасно вызывать повторно)."""
    root = logging.getLogger()
    if not any(isinstance(f, JobIdFilter) for f in root.filters):
        root.addFilter(JobIdFilter())
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(job_id)s%(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    for h in root.handlers:
        if not any(isinstance(f, JobIdFilter) for f in h.filters):
            h.addFilter(JobIdFilter())


__all__ = ["job_context", "setup_logging", "JOB_ID"]