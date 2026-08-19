"""Планировщик сцен.

Фразы для озвучки создаёт Python (split_into_phrases) — текст здесь не
изменяется. LLM возвращает ТОЛЬКО визуальные метаданные для каждой фразы:
  {"scenes": [{"phrase_indexes": [0, 3], "visual": "...", "keywords": [...], "duration_hint": 4}]}

Связывание фраз и сцен выполняет map_scenes_to_phrases(phrases, scenes).

ПЛОХО: technology / AI / news
ХОРОШО: AI researcher working at computer / server room / programmer typing code
"""
import logging
from dataclasses import dataclass, field

from ai import LLMError, LLMProvider, create_llm_provider
from script.analyzer import Analysis
from subtitles import split_into_phrases

logger = logging.getLogger(__name__)

PROMPT_SCENES = (
    "Ты — режиссёр вертикальных видео. Ниже дан список фраз (каждая фраза = "
    "одна реплика для озвучки). Для КАЖДОЙ фразы придумай видеоряд, вернув "
    "только метаданные — текст фраз НЕ менять и НЕ возвращать.\n\n"
    'Ответь JSON: {"scenes": [{"phrase_indexes": [номера фраз], '
    '"visual": "КОНКРЕТНОЕ английское описание кадра для поиска стокового видео '
    "(напр. AI researcher working at computer / server room / programmer typing code), "
    '"keywords": ["2-4 английских поисковых тега"], "duration_hint": 4}]}\n\n'
    "Правила:\n"
    "1. phrase_indexes — индексы фраз из списка (от 0). Одна сцена может "
    "покрывать несколько фраз, если им нужен один видеоряд.\n"
    "2. visual — конкретная сцена, НЕ абстракции (запрещено: technology, news, future).\n"
    "3. Число сцен: от 3 до {max_scenes}.\n"
    "4. Только JSON, без пояснений.\n\n"
    "Визуальные темы новости: {visual_keywords}\n\n"
    "Фразы (индекс: текст):\n{phrases}"
)


@dataclass
class Scene:
    """Визуальные метаданные, привязанные к индексам фраз (не текст!)."""

    visual: str = ""
    keywords: list[str] = field(default_factory=list)
    duration_hint: float = 4.0
    phrase_indexes: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "visual": self.visual,
            "keywords": self.keywords,
            "duration_hint": self.duration_hint,
            "phrase_indexes": self.phrase_indexes,
        }


@dataclass
class ScenePlan:
    scenes: list[Scene] = field(default_factory=list)

    def validate(self) -> bool:
        """Валидация сцен: phrase_indexes list[int] непустой, visual непустая
        str, keywords list[str], 0.5 <= duration_hint <= 30. Невалидные убирает."""
        valid: list[Scene] = []
        for s in self.scenes:
            if not isinstance(s.phrase_indexes, list) or not s.phrase_indexes:
                continue
            if not all(isinstance(i, int) for i in s.phrase_indexes):
                continue
            if not isinstance(s.visual, str) or not s.visual.strip():
                continue
            if not isinstance(s.keywords, list):
                continue
            try:
                hint = float(s.duration_hint)
            except (TypeError, ValueError):
                hint = 4.0
            s.duration_hint = max(0.5, min(30.0, hint))
            valid.append(s)
        self.scenes = valid
        return bool(valid)

    def to_dict(self) -> dict:
        return {"scenes": [s.to_dict() for s in self.scenes]}


def map_scenes_to_phrases(phrases: list[str], scenes: list[Scene]) -> list[Scene]:
    """Связывает фразы и сцены по phrase_indexes.

    Возвращает список сцен длиной == len(phrases): каждая фраза получает
    сцену (если фраза явно указана в phrase_indexes — свою, иначе —
    ближайшую/по индексу по кругу). text сцен не содержит — текст остаётся
    у фраз (это требование ТЗ: LLM не меняет текст).
    """
    if not phrases:
        return []
    scenes = scenes or []
    if not scenes:
        return []

    by_index: dict[int, Scene] = {}
    for scene in scenes:
        for idx in scene.phrase_indexes:
            if isinstance(idx, int) and 0 <= idx < len(phrases):
                by_index[idx] = scene

    result: list[Scene] = []
    scene_index = 0
    for i, _phrase in enumerate(phrases):
        if i in by_index:
            result.append(by_index[i])
            continue
        scene = scenes[scene_index % len(scenes)]
        result.append(scene)
        scene_index += 1
    return result


async def plan_scenes(
    text: str,
    analysis: Analysis,
    provider: LLMProvider | None = None,
    max_scenes: int = 12,
) -> ScenePlan:
    """Планирует сцены. При ошибке LLM — детерминированный fallback:
    фразы из текста, visual — из global keywords новости (сцены по кругу)."""
    text = text.strip()
    phrases = split_into_phrases(text)
    visual_keywords = analysis.visual_keywords or []

    if provider is None:
        provider = create_llm_provider()

    try:
        numbered = "\n".join(f"{i}: {p}" for i, p in enumerate(phrases))
        content = await provider.complete(
            PROMPT_SCENES.format(
                phrases=numbered[:2500],
                visual_keywords=", ".join(visual_keywords) or "technology",
                max_scenes=max_scenes,
            )
        )
        plan = ScenePlan(scenes=_parse_scenes(content))
        if plan.validate():
            logger.info("Спланировано сцен: %d", len(plan.scenes))
            return plan
    except (LLMError, Exception) as exc:
        logger.warning("Планирование сцен не удалось (%s), fallback", exc)

    # Fallback: фразы как есть, visual — по глобальным ключевым словам по кругу
    fallback = [
        Scene(
            visual=visual_keywords[i % len(visual_keywords)] if visual_keywords else "",
            keywords=[visual_keywords[i % len(visual_keywords)]] if visual_keywords else [],
            duration_hint=max(0.5, min(30.0, len(p) / 20)),
            phrase_indexes=[i],
        )
        for i, p in enumerate(phrases)
    ]
    logger.info("Сцены (fallback): %d", len(fallback))
    return ScenePlan(scenes=fallback)


def _parse_scenes(content: str) -> list[Scene]:
    """Парсит JSON от LLM. Поле text игнорируется — текст не меняем."""
    from utils.json_utils import extract_json

    data = extract_json(content)
    if not isinstance(data, dict):
        return []
    raw_scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(raw_scenes, list):
        return []

    scenes: list[Scene] = []
    for raw in raw_scenes:
        if not isinstance(raw, dict):
            continue
        visual = (raw.get("visual") or "").strip()
        raw_idx = raw.get("phrase_indexes") or []
        phrase_indexes: list[int] = []
        for idx in raw_idx:
            if isinstance(idx, int):
                phrase_indexes.append(idx)
            elif isinstance(idx, str) and idx.strip().lstrip("-").isdigit():
                phrase_indexes.append(int(idx.strip()))
        keywords = [k.strip() for k in (raw.get("keywords") or []) if isinstance(k, str) and k.strip()]
        if not phrase_indexes:
            continue
        try:
            duration_hint = float(raw.get("duration_hint") or 4.0)
        except (TypeError, ValueError):
            duration_hint = 4.0
        scenes.append(
            Scene(
                visual=visual,
                keywords=keywords,
                duration_hint=duration_hint,
                phrase_indexes=phrase_indexes,
            )
        )
    return scenes