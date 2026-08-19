"""Сущности news batch: NewsItem, NewsBatch, TimelineItem, UnifiedTimeline.

NewsBatch — результат обработки нескольких новостей локальной LLM.
Каждая новость имеет собственный ID (news_id). Порядок — массив id.
"""
from dataclasses import dataclass, field


@dataclass
class NewsItem:
    """Одна новость в batch. id — сквозной (1..N), не меняется при сортировке."""

    id: int
    original_text: str = ""
    edited_text: str = ""
    title: str = ""
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    importance: float = 0.5
    category: str = "other"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "original_text": self.original_text,
            "edited_text": self.edited_text,
            "title": self.title,
            "summary": self.summary,
            "keywords": self.keywords,
            "importance": self.importance,
            "category": self.category,
        }


@dataclass
class Transition:
    from_id: int
    to_id: int
    text: str


@dataclass
class NewsBatch:
    """Полный batch: отредактированные новости, порядок, intro/outro/transitions."""

    batch_id: str = ""
    news: list[NewsItem] = field(default_factory=list)
    order: list[int] = field(default_factory=list)
    intro: str = ""
    outro: str = ""
    transitions: list[Transition] = field(default_factory=list)

    def news_by_id(self, news_id: int) -> NewsItem | None:
        for item in self.news:
            if item.id == news_id:
                return item
        return None

    def ordered_news(self) -> list[NewsItem]:
        """Новости в порядке отображения (self.order), без неизвестных id."""
        by_id = {n.id: n for n in self.news}
        result = [by_id[i] for i in self.order if i in by_id]
        # добавляем те, что не попали в order (на всякий случай)
        seen = {i.id for i in result}
        result += [n for n in self.news if n.id not in seen]
        return result

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "news": [n.to_dict() for n in self.news],
            "order": self.order,
            "intro": self.intro,
            "outro": self.outro,
            "transitions": [
                {"from": t.from_id, "to": t.to_id, "text": t.text} for t in self.transitions
            ],
        }


# --- Timeline ---

# Типы элементов таймлайна
ITEM_INTRO = "intro"
ITEM_NEWS = "news"
ITEM_TRANSITION = "transition"
ITEM_OUTRO = "outro"


@dataclass
class TimelineItem:
    """Один сегмент unified timeline.

    id        — уникальный в рамках ролика (например 'n1', 't1-2').
    type      — intro | news | transition | outro
    news_id   — id новости (для news), иначе None.
    start/end — абсолютные секунды в финальном видео.
    duration  — end - start.
    text      — озвучиваемый текст сегмента.
    audio_path — путь к TTS-аудио сегмента.
    video_paths — список клипов [(путь, длительность_сегмента, сдвиг)].
    """

    id: str
    type: str
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0
    news_id: int | None = None
    text: str = ""
    audio_path: str = ""
    video_paths: list = field(default_factory=list)
    transition: str = ""  # fade | crossfade (заполняется при рендере)
    phrase_timings: list | None = None  # [(start, end)] фраз (word-level ASR), None = пропорционально

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "news_id": self.news_id,
            "text": self.text,
            "audio_path": self.audio_path,
            "video_paths": [
                [str(p), dur, off] for p, dur, off in (self.video_paths or [])
            ],
            "transition": self.transition,
            "phrase_timings": self.phrase_timings,
        }


@dataclass
class UnifiedTimeline:
    """Список сегментов с абсолютными таймингами. Без overlap и gap."""

    items: list[TimelineItem] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((i.end for i in self.items), default=0.0)

    def validate(self) -> list[str]:
        """Проверяет отсутствие overlap, negative duration и gap."""
        errors: list[str] = []
        prev_end = 0.0
        for i, item in enumerate(self.items):
            if item.duration < 0:
                errors.append(f"item {item.id}: negative duration {item.duration}")
            if abs(item.duration - (item.end - item.start)) > 0.05:
                errors.append(f"item {item.id}: duration != end-start")
            if item.start < -0.05:
                errors.append(f"item {item.id}: start < 0")
            if item.start < prev_end - 0.05:
                errors.append(f"item {item.id}: overlap с предыдущим (start {item.start} < prev_end {prev_end})")
            if item.start > prev_end + 0.05 and i > 0:
                errors.append(f"item {item.id}: gap (start {item.start} > prev_end {prev_end})")
            prev_end = max(prev_end, item.end)
        return errors

    def to_dict(self) -> dict:
        return {
            "duration": self.duration,
            "items": [i.to_dict() for i in self.items],
        }


# --- UnifiedScript ---

@dataclass
class UnifiedScript:
    """Сценарий выпуска: INTRO, NEWS, TRANSITION, ..., OUTRO.
    Каждый блок знает свой news_id (для субтитров/таймлайна)."""

    intro: str = ""
    outro: str = ""
    # (type, news_id, text) — последовательность блоков
    blocks: list[tuple[str, int | None, str]] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return " ".join(text for _, _, text in self.blocks if text)

    def items(self):
        """[(type, news_id, text)] — пары для построения timeline."""
        return list(self.blocks)


__all__ = [
    "NewsItem",
    "Transition",
    "NewsBatch",
    "TimelineItem",
    "UnifiedTimeline",
    "UnifiedScript",
    "ITEM_INTRO",
    "ITEM_NEWS",
    "ITEM_TRANSITION",
    "ITEM_OUTRO",
]