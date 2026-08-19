"""Локальный ASR: word-level тайминг фраз для сегментов таймлайна.

Использует whisper-timestamped singleton (грузится один раз, см.
subtitles.alignment._get_whisper_model). Для каждого сегмента таймлайна
запускается forced alignment; тайминги возвращаются ОТНОСИТЕЛЬНО начала
audio сегмента. Fallback при недоступности/таймауте — пропорциональный
(phrase_timings=None), вызывающий код сам перейдёт на build_timings().
"""
import asyncio
import logging

import config
from news.models import TimelineItem
from subtitles import build_timings_aligned, split_into_phrases

logger = logging.getLogger(__name__)


async def align_segment(
    item: TimelineItem,
    language: str = "ru",
) -> list[tuple[float, float]] | None:
    """Word-level тайминги фраз сегмента (относительно начала audio).

    Пропорциональный fallback, если whisper недоступен или текст не
    выровнялся. При недоступности ASR (LOCAL_ASR_ENGINE != whisper) —
    сразу None (пропорционально).
    """
    if config.LOCAL_ASR_ENGINE != "whisper":
        return None
    if not item.audio_path or not item.text:
        return None

    phrases = split_into_phrases(item.text)
    if not phrases:
        return None

    try:
        timings = await build_timings_aligned(phrases, item.audio_path, language)
    except Exception as exc:
        logger.warning("ASR-выравнивание сегмента %s упало (%s)", item.id, exc)
        timings = None
    return timings


async def align_timeline(timeline) -> None:
    """Выравнивает все сегменты таймлайна (item.phrase_timings).

    Каждый сегмент выравнивается отдельно (news_id остаётся в item.news_id,
    тайминги относительные — абсолютные сдвиги добавляются при рендере ASS).
    """
    if config.LOCAL_ASR_ENGINE != "whisper":
        return
    for item in timeline.items:
        timings = await align_segment(item)
        if timings:
            item.phrase_timings = timings
        await asyncio.sleep(0)


__all__ = ["align_segment", "align_timeline"]