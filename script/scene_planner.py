"""Планировщик сцен.

Сценарий разбивается на сцены. Каждая сцена — фрагмент текста с конкретным
визуальным описанием (visual) и keywords для поиска видео.

ПЛОХО: technology / AI / news
ХОРОШО: AI researcher working at computer / server room / programmer typing code

Визуальные описания максимально пригодны для поиска стоковых видео.
"""
import logging
import re
from dataclasses import dataclass, field

from ai import LLMError, LLMProvider, create_llm_provider
from script.analyzer import Analysis
from subtitles import split_into_phrases

logger = logging.getLogger(__name__)

PROMPT_SCENES = (
    "Ты — режиссёр вертикальных видео. Разбей текст на сцены (каждая сцена = "
    "одна фраза для озвучки, которой нужен свой видеоряд).\n\n"
    "Для КАЖДОЙ сцены верни:\n"
    '{"scenes": [{"text": "фраза (как в тексте, без изменений)", '
    '"visual": "КОНКРЕТНОЕ английское описание кадра для поиска стокового видео '
    "(напр. AI researcher working at computer / server room / programmer typing code), "
    '"keywords": ["2-4 английских поисковых тега"], "duration_hint": 4}]}\n\n'
    "Правила:\n"
    "1. text — точный фрагмент из исходного текста (не меняй слова).\n"
    "2. visual — конкретная сцена, НЕ абстракции (запрещено: technology, news, future).\n"
    "3. Число сцен: от 3 до {max_scenes}.\n"
    "4. Только JSON, без пояснений.\n\n"
    "Визуальные темы новости: {visual_keywords}\n\nТекст:\n{text}"
)


@dataclass
class Scene:
    text: str = ""
    visual: str = ""
    keywords: list[str] = field(default_factory=list)
    duration_hint: float = 4.0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "visual": self.visual,
            "keywords": self.keywords,
            "duration_hint": self.duration_hint,
        }


@dataclass
class ScenePlan:
    scenes: list[Scene] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"scenes": [s.to_dict() for s in self.scenes]}


async def plan_scenes(
    text: str,
    analysis: Analysis,
    provider: LLMProvider | None = None,
    max_scenes: int = 12,
) -> ScenePlan:
    """Планирует сцены. При ошибке LLM — детерминированный fallback:
    фразы из текста с visual из global keywords новости."""
    text = text.strip()
    phrases = split_into_phrases(text)
    visual_keywords = analysis.visual_keywords or []

    if provider is None:
        provider = create_llm_provider()

    try:
        content = await provider.complete(
            PROMPT_SCENES.format(
                text=text[:2500],
                visual_keywords=", ".join(visual_keywords) or "technology",
                max_scenes=max_scenes,
            )
        )
        parsed = _parse_scenes(content, phrases)
        if parsed:
            logger.info("Спланировано сцен: %d", len(parsed))
            return ScenePlan(scenes=parsed)
    except (LLMError, Exception) as exc:
        logger.warning("Планирование сцен не удалось (%s), fallback", exc)

    # Fallback: фразы как есть, visual — по глобальным ключевым словам по кругу
    fallback = [
        Scene(
            text=p,
            visual=visual_keywords[i % len(visual_keywords)] if visual_keywords else "",
            keywords=[visual_keywords[i % len(visual_keywords)]] if visual_keywords else [],
            duration_hint=max(3.0, min(6.0, len(p) / 20)),
        )
        for i, p in enumerate(phrases)
    ]
    logger.info("Сцены (fallback): %d", len(fallback))
    return ScenePlan(scenes=fallback)


def _parse_scenes(content: str, source_phrases: list[str]) -> list[Scene]:
    if not content:
        return []
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        import json
        data = json.loads(cleaned[start:end + 1])
    except Exception:
        return []
    raw_scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(raw_scenes, list):
        return []

    # Привязываем сцены к исходным фразам по совпадению текста, чтобы не потерять тайминг.
    # Если тексты не совпадают — используем фразы из LLM, но сохраняем визуалы.
    scenes: list[Scene] = []
    for raw in raw_scenes:
        if not isinstance(raw, dict):
            continue
        stext = (raw.get("text") or "").strip()
        if not stext:
            continue
        visual = (raw.get("visual") or "").strip()
        keywords = [k.strip() for k in (raw.get("keywords") or []) if isinstance(k, str) and k.strip()]
        try:
            duration_hint = float(raw.get("duration_hint") or 4.0)
        except (TypeError, ValueError):
            duration_hint = 4.0
        scenes.append(Scene(text=stext, visual=visual, keywords=keywords, duration_hint=duration_hint))
    return scenes