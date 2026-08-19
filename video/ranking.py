"""Ранжирование кандидатов-клипов.

Итоговый score — взвешенная сумма нормализованных факторов (веса прозрачны
и зафиксированы в WEIGHTS, см. ТЗ v2.0.1):

    score = relevance*0.40 + orientation*0.20 + resolution*0.15
          + duration*0.10 + keyword*0.15

Каждый фактор принимает значение в [0, 1], поэтому итог — в [0, 1].
Штрафы (отдельно): повторный клип (used) — жёсткий отказ (-1000),
слишком короткий клип.

Дубликаты: если для фразы есть альтернатива (score > 0) — повторный клип
не выбирается; если свободных альтернатив нет — разрешается повтор
лучшего из уже использованных.
"""
import logging
import re
from dataclasses import dataclass, field

import config
from video_source import VideoClip

logger = logging.getLogger(__name__)

# Прозрачные веса факторов (сумма = 1.0)
WEIGHTS = {
    "relevance": 0.40,
    "orientation": 0.20,
    "resolution": 0.15,
    "duration": 0.10,
    "keyword": 0.15,
}


@dataclass
class ScoredClip:
    clip: VideoClip
    score: float
    reasons: list[str] = field(default_factory=list)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9]{2,}", (text or "").lower()))


def keyword_overlap(query: str, keywords: list[str]) -> int:
    """Сколько слов запроса/ключей встречается в keywords кандидата."""
    q_tokens = _tokens(query)
    key_tokens: set[str] = set()
    for k in keywords:
        key_tokens |= _tokens(k)
    return len(q_tokens & key_tokens)


def _factor_relevance(clip: VideoClip, query: str) -> float:
    """Насколько clip.query совпадает с искомым query/visual. [0,1]"""
    if not query:
        return 0.5
    cq = (clip.query or "").lower()
    q = query.lower()
    if q in cq or cq in q:
        return 1.0
    overlap = keyword_overlap(clip.query, [query])
    return min(overlap, 2) / 2


def _factor_orientation(clip: VideoClip) -> float:
    """Портрет/квадрат хорошо для вертикали, горизонталь — хуже. [0,1]"""
    w, h = clip.width or 0, clip.height or 0
    if not w or not h:
        return 0.5
    if h >= w * 1.2:
        return 1.0  # портрет
    if h >= w * 0.9:
        return 0.8  # квадрат
    return 0.2  # горизонталь


def _factor_resolution(clip: VideoClip) -> float:
    """Чем ближе к 1080 по ширине — тем лучше. [0,1]"""
    w = clip.width or 0
    if not w:
        return 0.0
    if w < config.MIN_CLIP_WIDTH:
        return 0.1
    return min(w, 1080) / 1080


def _factor_duration(clip: VideoClip, min_duration: float) -> float:
    """Достаточно длинный клип — лучше. [0,1]"""
    d = clip.duration or 0
    if d <= 0:
        return 0.3
    if d < 2.0:
        return 0.1
    if min_duration and d < min_duration:
        return 0.4
    return min(d, 30) / 30


def _factor_keyword(clip: VideoClip, scene_keywords: list[str]) -> float:
    """Совпадение keywords фразы с запросом клипа. [0,1]"""
    if not scene_keywords:
        return 0.0
    overlap = keyword_overlap(clip.query, scene_keywords)
    return min(overlap, 4) / 4


def score_clip(
    clip: VideoClip,
    query: str,
    scene_keywords: list[str] | None = None,
    used_ids: set[str] | None = None,
    min_duration: float = 0.0,
) -> ScoredClip:
    """Считает score кандидата в [0,1] по взвешенным факторам.

    Повторный клип -> -1000 (жёсткий отказ). Логирует debug-разбивку.
    """
    used_ids = used_ids or set()
    scene_keywords = scene_keywords or []
    reasons: list[str] = []

    # Дубликат — жёсткий отказ
    if clip.id in used_ids:
        logger.debug("Клип %s уже использован — отказ", clip.id)
        return ScoredClip(clip, -1000.0, ["дубликат"])

    relevance = _factor_relevance(clip, query)
    orientation = _factor_orientation(clip)
    resolution = _factor_resolution(clip)
    duration = _factor_duration(clip, min_duration)
    keyword = _factor_keyword(clip, scene_keywords)

    score = (
        relevance * WEIGHTS["relevance"]
        + orientation * WEIGHTS["orientation"]
        + resolution * WEIGHTS["resolution"]
        + duration * WEIGHTS["duration"]
        + keyword * WEIGHTS["keyword"]
    )
    score = round(score, 3)

    for name, value in (
        ("релевантность", relevance),
        ("ориентация", orientation),
        ("разрешение", resolution),
        ("длительность", duration),
        ("ключи", keyword),
    ):
        reasons.append(f"{name}={value:.2f}")
    logger.debug("score_clip %s: score=%.3f (%s)", clip.id, score, ", ".join(reasons))

    return ScoredClip(clip, score, reasons)