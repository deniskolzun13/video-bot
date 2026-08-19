"""Сортировка новостей (раздел 16).

Учитывает importance, интересность (длина/детализация), логическую
последовательность (LLM в PHASE 3 может переупорядочить, здесь —
детерминированный базовый порядок), recency, если дата присутствует.
"""
import logging
import re
from datetime import date

from news.models import NewsItem

logger = logging.getLogger(__name__)

# Русские месяцы для парсинга дат в тексте новости
MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.IGNORECASE)


def _extract_date(text: str) -> date | None:
    """Пытается вытащить дату вида '15 мая 2026' из текста."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    month = MONTHS_RU.get(month_name)
    if not month or not 1 <= day <= 31 or not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _interest_score(item: NewsItem) -> float:
    """Интересность: длина отредактированного текста (умеренно) + keywords."""
    base = min(len(item.edited_text or "") / 300, 1.0) * 0.5
    kw = min(len(item.keywords or []) / 5, 1.0) * 0.5
    return base + kw


class NewsOrderingService:
    """Определяет порядок новостей в выпуске."""

    def order(self, items: list[NewsItem]) -> list[int]:
        """Возвращает список id в порядке отображения.

        Приоритет: recency (свежие даты первыми, если есть) -> importance ->
        интересность. Внутри равных — по исходному порядку (стабильность).
        """
        if not items:
            return []
        scored = []
        for i, item in enumerate(items):
            d = _extract_date(item.original_text)
            recency = d.toordinal() if d else None  # None — нет даты
            scored.append((item, i, item.importance, _interest_score(item), recency))
        # Сначала с датами (свежие — выше), потом без дат по importance
        with_date = [s for s in scored if s[4] is not None]
        without_date = [s for s in scored if s[4] is None]
        with_date.sort(key=lambda s: (-s[4], -s[2], -s[3], s[1]))
        without_date.sort(key=lambda s: (-s[2], -s[3], s[1]))
        ordered = with_date + without_date
        return [s[0].id for s in ordered]


order_news = NewsOrderingService().order

__all__ = ["NewsOrderingService", "order_news", "_extract_date", "_interest_score"]