"""Ранжирование кандидатов-клипов.

Каждый кандидат получает score по сумме факторов:
  + совпадение keywords
  + подходящая ориентация (портрет для вертикали)
  + разрешение (ближе к 1080 — лучше)
  + длительность (не слишком короткая)
  + релевантность запроса
Штрафы:
  - повторный клип (уже использован)
  - слишком короткий клип
  - неподходящее разрешение (< MIN_CLIP_WIDTH)
  - плохое совпадение запроса

Код расширяемый: добавь свой фактор в score_clip — он сразу учтётся.
"""
import re
from dataclasses import dataclass, field

import config
from video_source import VideoClip


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


def score_clip(
    clip: VideoClip,
    query: str,
    scene_keywords: list[str] | None = None,
    used_ids: set[str] | None = None,
    min_duration: float = 0.0,
) -> ScoredClip:
    """Считает score кандидата. Чем больше — тем лучше."""
    used_ids = used_ids or set()
    scene_keywords = scene_keywords or []
    score = 0.0
    reasons: list[str] = []

    # 1. Повторный клип — жёсткий штраф
    if clip.id in used_ids:
        return ScoredClip(clip, -1000.0, ["дубликат"])

    # 2. Ориентация: портрет/квадрат лучше для вертикального видео
    w, h = clip.width or 0, clip.height or 0
    if w and h:
        if h >= w * 1.2:
            score += 30
            reasons.append("портрет")
        elif h >= w * 0.9:
            score += 15
            reasons.append("квадрат")
        else:
            score -= 5  # горизонтальный — спад, но не отказ (Steam-трейлеры 16:9)

    # 3. Разрешение: ближе к 1080 по ширине — лучше; ниже MIN_CLIP_WIDTH — штраф
    if w:
        if w < config.MIN_CLIP_WIDTH:
            score -= 25
            reasons.append(f"мало {w}px")
        else:
            score += min(w, 1080) / 1080 * 20
            if w >= 1080:
                reasons.append(f"{w}px")

    # 4. Длительность: не слишком короткая
    if clip.duration and min_duration:
        if clip.duration < 2.0:
            score -= 30
            reasons.append("короткий")
        elif clip.duration < min_duration:
            score -= 10

    # 5. Совпадение keywords/запроса
    if scene_keywords:
        clip_query_tokens = _tokens(clip.query)
        key_tokens = set()
        for k in scene_keywords:
            key_tokens |= _tokens(k)
        overlap = len(clip_query_tokens & key_tokens)
        score += overlap * 8
        if overlap:
            reasons.append(f"совпадение x{overlap}")
    if query and query.lower() in (clip.query or "").lower():
        score += 10
        reasons.append("точный запрос")

    return ScoredClip(clip, round(score, 2), reasons)