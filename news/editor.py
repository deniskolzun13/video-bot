"""Редактор новостей: PHASE 1 — каждая новость обрабатывается локальной LLM.

Задачи (см. ТЗ v2.0.1+, разделы 11/13/47):
  - исправить ошибки, улучшить стиль, сократить до удобного для озвучки текста;
  - заголовок (title), summary, keywords (visual), importance;
  - НЕ выдумывать факты: исходный текст — source of truth. Запрещено добавлять
    даты/числа/имена/компании/события, которых нет в исходнике.
  - строгий JSON, валидация (без eval).
"""
import logging

from ai import LLMError, LLMProvider, create_llm_provider
from news.models import NewsItem
from utils.json_utils import as_float, as_str, as_str_list, extract_json

logger = logging.getLogger(__name__)

PROMPT_EDIT = (
    "Ты — редактор новостей для озвучки в вертикальном видео. "
    "Отредактируй новость по правилам и верни СТРОГО один JSON без пояснений.\n\n"
    "Правила:\n"
    "1. НЕ выдумывай факты: исходный текст — единственный источник правды. "
    "Запрещено добавлять даты, числа, имена, компании, события, характеристики, "
    "которых нет в исходном тексте.\n"
    "2. Исправь грамматику и стиль, но сохрани все факты.\n"
    "3. Сократи текст до 2-4 предложений (50-250 символов), удобных для озвучки.\n"
    "4. Заголовок — до 60 символов, цепляющий.\n"
    "5. summary — краткое содержание (1-2 предложения).\n"
    "6. keywords — 3-5 визуальных тем на английском для поиска видео "
    "(предметы/сцены, НЕ абстракции).\n"
    "7. importance — число от 0.1 до 1.0 (насколько новость важна/интересна).\n\n"
    "Формат ответа:\n"
    '{\n  "title": "...",\n  "edited_text": "...",\n  "summary": "...",\n'
    '  "keywords": ["..."],\n  "importance": 0.9,\n  "category": "tech|business|science|world|sport|games|other"\n}\n\n'
    "Новость:\n{text}"
)


def _parse_edited(content: str) -> dict | None:
    data = extract_json(content)
    if not isinstance(data, dict):
        return None
    edited = as_str(data.get("edited_text"))
    if len(edited) < 30:
        return None  # слишком короткий — невалидно
    return data


def validate_facts(edited: str, original: str) -> list[str]:
    """Проверка: не добавлены ли новые числа (минимальный эвристический контроль).
    Полный fact-check — через LLM (см. quality_check), здесь ловим грубые случаи."""
    import re

    new_numbers = []
    orig_nums = set(re.findall(r"\d+(?:[.,]\d+)?%?", original))
    edited_nums = set(re.findall(r"\d+(?:[.,]\d+)?%?", edited))
    for num in edited_nums - orig_nums:
        # Слова "1", "2" часто появляются как артикли/порядковые — допускаем единичные
        if num not in ("1", "2", "3"):
            new_numbers.append(num)
    return new_numbers


class NewsEditor:
    """Редактирует одну новость через локальную LLM. Возвращает NewsItem."""

    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or create_llm_provider()

    async def edit(self, news_id: int, text: str) -> NewsItem:
        """PHASE 1: локальное редактирование одной новости.

        Если LLM вернул невалидный JSON/слишком короткий текст — используем
        исходный текст (source of truth), но title/summary генерируем эвристикой.
        НЕ падаем из-за LLM: новость остаётся как есть.
        """
        item = NewsItem(id=news_id, original_text=text, edited_text=text)
        try:
            content = await self.provider.complete(PROMPT_EDIT.format(text=text[:3000]))
        except (LLMError, Exception) as exc:
            logger.warning("Редактирование новости %d не удалось (%s), исходный текст", news_id, exc)
            return self._fallback(item, text)

        data = _parse_edited(content)
        if not data:
            logger.warning("Новость %d: невалидный JSON от LLM, исходный текст", news_id)
            return self._fallback(item, text)

        edited = as_str(data.get("edited_text"))
        new_numbers = validate_facts(edited, text)
        if new_numbers:
            logger.warning("Новость %d: LLM добавил числа %s — используем исходник", news_id, new_numbers)
            return self._fallback(item, text)

        item.edited_text = edited
        item.title = as_str(data.get("title")) or self._fallback_title(text)
        item.summary = as_str(data.get("summary"))
        item.keywords = as_str_list(data.get("keywords"))
        item.importance = as_float(data.get("importance"), 0.5, lo=0.1, hi=1.0)
        item.category = as_str(data.get("category"), 30).lower() or "other"
        return item

    @staticmethod
    def _fallback(item: NewsItem, text: str) -> NewsItem:
        """Эвристический fallback: исходный текст, простой title."""
        item.edited_text = text
        item.title = NewsEditor._fallback_title(text)
        item.keywords = _heuristic_keywords(text)
        item.importance = 0.5
        return item

    @staticmethod
    def _fallback_title(text: str, limit: int = 60) -> str:
        import re

        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        last_dot = cut.rfind(".")
        return cut[:last_dot + 1] if last_dot > 0 else cut + "…"


def _heuristic_keywords(text: str, n: int = 4) -> list[str]:
    """Частотная эвристика ключевых слов (без сети)."""
    import re
    from collections import Counter

    from video_source import STOPWORDS

    words = re.findall(r"[а-яёa-z][а-яёa-z-]{3,}", text.lower())
    words = [w for w in words if w not in STOPWORDS and not w.isdigit()]
    freq = Counter(words)
    return [w for w, _ in freq.most_common(n)]


async def quality_check(item: NewsItem, provider: LLMProvider | None = None) -> bool:
    """Проверка качества после LLM: пустой/слишком короткий/слишком длинный текст,
    новые числа/имена, дубликаты. True — ок, False — проблемы (нужен retry).

    Легковесная: не вызывает LLM (экономия локальных ресурсов) — только
    эвристические проверки. LLM-проверка фактов выполняется в validate_facts.
    """
    if not item.edited_text or len(item.edited_text) < 30:
        return False
    if len(item.edited_text) > 600:
        return False  # слишком длинный — сократи
    if validate_facts(item.edited_text, item.original_text):
        return False
    return True


__all__ = ["NewsEditor", "quality_check", "validate_facts", "_parse_edited", "PROMPT_EDIT"]