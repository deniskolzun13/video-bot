"""Анализатор текста новости через LLM.

Вход: обычный текст новости.
Выход: структурированный объект Analysis (topic, title, main_subject, category,
entities, keywords, visual_keywords). Если LLM не отвечает или возвращает мусор —
работает детерминированный fallback (эвристика по частоте слов).
"""
import logging
import re
from dataclasses import asdict, dataclass, field

from ai import LLMError, LLMProvider, create_llm_provider
from utils.json_utils import as_dict, as_str, as_str_list, extract_json
from video_source import extract_keywords_heuristic, translate_keywords

logger = logging.getLogger(__name__)

PROMPT_ANALYZE = (
    "Ты — редактор новостей. Проанализируй новость и верни СТРОГО один JSON-объект "
    "без пояснений, без markdown-разметки, без ```json``` обёртки:\n"
    "{\n"
    '  "topic": "краткая тема на русском (2-5 слов)",\n'
    '  "title": "заголовок новости на русском",\n'
    '  "main_subject": "главный объект новости (кто/что) на русском",\n'
    '  "category": "одна из: games, tech, business, science, world, sport, entertainment, other",\n'
    '  "entities": ["известные имена/бренды/названия из текста"],\n'
    '  "keywords": ["ключевые слова на русском"],\n'
    '  "visual_keywords": ["4-6 конкретных английских визуальных тем для поиска стоковых '
    'видео (предметы, сцены, люди за работой; НЕ абстракции)"]\n'
    "}\n\n"
    "Правила: только JSON; visual_keywords на английском, конкретные; "
    "запрещены пустые значения.\n\nНовость:\n{text}"
)


@dataclass
class Analysis:
    topic: str = ""
    title: str = ""
    main_subject: str = ""
    category: str = "other"
    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    visual_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_json(content: str) -> dict | None:
    """Извлекает JSON из ответа LLM (устойчив к ```json``` обёртке и мусору)."""
    return as_dict(extract_json(content))


def _clean_str(value: str, limit: int = 200) -> str:
    return as_str(value, limit=limit)


def _clean_list(value) -> list[str]:
    return as_str_list(value)


def _validate(data: dict | None) -> Analysis | None:
    if not data or not isinstance(data, dict):
        return None
    try:
        return Analysis(
            topic=_clean_str(data.get("topic")) or _clean_str(data.get("title", ""))[:60],
            title=_clean_str(data.get("title")),
            main_subject=_clean_str(data.get("main_subject")) or _clean_str(data.get("topic")),
            category=_clean_str(data.get("category"), 30).lower(),
            entities=_clean_list(data.get("entities")),
            keywords=_clean_list(data.get("keywords")),
            visual_keywords=_clean_list(data.get("visual_keywords")),
        )
    except Exception as exc:
        logger.warning("Не удалось собрать Analysis: %s", exc)
        return None


async def _fallback_analysis(text: str) -> Analysis:
    """Детерминированный fallback без LLM."""
    keywords = extract_keywords_heuristic(text, n=8)
    visual = await translate_keywords(keywords[:4])
    title = _make_title(text)
    return Analysis(
        topic=keywords[0] if keywords else "Новость",
        title=title,
        main_subject=keywords[0] if keywords else "",
        category="other",
        entities=[],
        keywords=keywords,
        visual_keywords=visual,
    )


def _make_title(text: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_dot = cut.rfind(".")
    return cut[:last_dot + 1] if last_dot > 0 else cut + "…"


async def analyze_text(
    text: str,
    provider: LLMProvider | None = None,
) -> Analysis:
    """Анализирует новость. Никогда не падает: при ошибке LLM — fallback."""
    text = text.strip()[:3000]
    if not text:
        return Analysis()

    if provider is None:
        provider = create_llm_provider()

    try:
        content = await provider.complete(PROMPT_ANALYZE.format(text=text))
        analysis = _validate(_extract_json(content))
        if analysis and analysis.visual_keywords:
            logger.info("Анализ текста: topic=%s, category=%s, visual=%s",
                        analysis.topic, analysis.category, analysis.visual_keywords[:3])
            return analysis
    except (LLMError, Exception) as exc:
        logger.warning("LLM-анализ не удался (%s), fallback на эвристику", exc)

    fallback = await _fallback_analysis(text)
    logger.info("Анализ текста (fallback): topic=%s, visual=%s",
                fallback.topic, fallback.visual_keywords[:3])
    return fallback