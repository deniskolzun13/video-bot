"""Построение UnifiedTimeline из UnifiedScript + аудио + видео (разделы 19-20, 31).

TimelineItem: intro / news / transition / outro с абсолютными start/end.
Без overlap, negative duration и gap (проверяется в UnifiedTimeline.validate).

Title card (NEWS_TITLE_ENABLED) добавляет NEWS_TITLE_DURATION секунд к началу
каждого news-сегмента (видео без озвучки, заголовок поверх).
"""
import logging
import uuid
from dataclasses import dataclass

import config
from news.models import (
    ITEM_NEWS,
    TimelineItem,
    UnifiedScript,
    UnifiedTimeline,
)

logger = logging.getLogger(__name__)


@dataclass
class SegmentAudio:
    """Аудио сегмента: путь + длительность (от TTS)."""

    block_key: str
    text: str
    audio_path: str
    duration: float


def build_timeline(
    script: UnifiedScript,
    segment_audio: dict[str, SegmentAudio],
    news_titles: dict[int, str] | None = None,
    width: int = 0,
    height: int = 0,
) -> UnifiedTimeline:
    """Строит UnifiedTimeline по сценарию и аудио каждого блока.

    segment_audio: {block_key: SegmentAudio}, где block_key:
        "intro", "outro", f"news:{news_id}", f"transition:{from_id}-{to_id}"
    news_titles: {news_id: title} — для title card.
    width/height — используются для документации (не критично для таймлайна).

    Возвращает таймлайн. Каждый news-сегмент получает +NEWS_TITLE_DURATION
    (title card в начале, если NEWS_TITLE_ENABLED).
    """
    timeline = UnifiedTimeline(items=[])
    cursor = 0.0
    news_titles = news_titles or {}
    used_ids: set[str] = set()

    def _make_id(prefix: str) -> str:
        base = f"{prefix}"
        while base in used_ids:
            base = f"{prefix}-{uuid.uuid4().hex[:4]}"
        used_ids.add(base)
        return base

    for block_type, news_id, text in script.blocks:
        key = _block_key(block_type, news_id)
        seg = segment_audio.get(key)
        if seg is None or not seg.audio_path:
            logger.warning("Нет аудио для блока %s — пропускаем", key)
            continue

        duration = seg.duration
        item_type = block_type
        item_news_id = news_id

        if block_type == "news" and config.NEWS_TITLE_ENABLED:
            duration += config.NEWS_TITLE_DURATION
            item_type = ITEM_NEWS

        item_id = _make_id(key.replace(":", "-"))
        timeline.items.append(
            TimelineItem(
                id=item_id,
                type=item_type,
                start=cursor,
                end=cursor + duration,
                duration=duration,
                news_id=item_news_id,
                text=seg.text,
                audio_path=seg.audio_path,
            )
        )
        cursor += duration

    errors = timeline.validate()
    if errors:
        logger.warning("Timeline невалиден: %s", "; ".join(errors))
    logger.info("Timeline построен: %d сегментов, длительность %.1f с",
                len(timeline.items), timeline.duration)
    return timeline


def _block_key(block_type: str, news_id: int | None) -> str:
    if block_type == "news":
        return f"news:{news_id}"
    if block_type == "transition":
        # transition:news_id — переход ПОСЛЕ этой новости (from_id)
        return f"transition:{news_id}"
    return block_type  # intro / outro


__all__ = ["build_timeline", "SegmentAudio", "_block_key"]