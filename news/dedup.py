"""Дедупликация новостей (PHASE 2, раздел 15).

Определяет одинаковые/повторные новости и продолжения одного события.
НЕ удаляет новости без достаточной уверенности.
"""
import logging
import re
from difflib import SequenceMatcher

from news.models import NewsItem

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Нижний регистр, снятие пунктуации, схлопывание пробелов."""
    text = re.sub(r"\s+", " ", (text or "").lower())
    text = re.sub(r"[^a-zа-яё0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    """Сходство 0..1 по SequenceMatcher (не требует внешних зависимостей)."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _keyword_overlap(item_a: NewsItem, item_b: NewsItem) -> float:
    """Совпадение ключевых слов 0..1."""
    ka = set(k.lower() for k in (item_a.keywords or []))
    kb = set(k.lower() for k in (item_b.keywords or []))
    if not ka or not kb:
        return 0.0
    return len(ka & kb) / max(len(ka | kb), 1)


def deduplicate(items: list[NewsItem], text_threshold: float = 0.85,
                overlap_threshold: float = 0.6) -> tuple[list[NewsItem], list[int]]:
    """Возвращает (уникальные новости, id удалённых).

    Правила:
      - текст почти совпадает (>= 0.85) — дубликат;
      - высокое совпадение ключевых слов И текстовое сходство >= 0.6 — повтор
        одного события;
    НЕ удаляет новости, если уверенности мало.
    """
    unique: list[NewsItem] = []
    removed_ids: list[int] = []

    for item in items:
        is_dup = False
        for kept in unique:
            sim = _similarity(item.original_text, kept.original_text)
            overlap = _keyword_overlap(item, kept)
            if sim >= text_threshold:
                is_dup = True
                break
            if sim >= overlap_threshold and overlap >= 0.6:
                # "продолжение одного события" — считаем повтором только если
                # совпадают и текст, и ключевые слова достаточно сильно
                is_dup = True
                break
        if is_dup:
            logger.info("Дубликат найден: новость %d похожа на %d (sim=%.2f)",
                        item.id, kept.id, _similarity(item.original_text, kept.original_text))
            removed_ids.append(item.id)
        else:
            unique.append(item)

    if removed_ids:
        logger.info("Дедупликация: удалено %d из %d новостей", len(removed_ids), len(items))
    return unique, removed_ids


__all__ = ["deduplicate", "_similarity", "_normalize", "_keyword_overlap"]