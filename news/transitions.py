"""Планировщик переходов (раздел 18).

Переходы короткие, НЕ содержат новых фактов. Используются между новостями.
Генерация через локальную LLM (PHASE 3, компактный промпт), при недоступности —
детерминированный набор шаблонов.
"""
import logging

from ai import LLMError, LLMProvider, create_llm_provider
from news.models import NewsItem, Transition

logger = logging.getLogger(__name__)

# Детерминированные шаблоны (короткие, без фактов)
TEMPLATES = [
    "А теперь к следующей новости.",
    "Тем временем...",
    "Но на этом новости не заканчиваются.",
    "Перейдём к следующему событию.",
    "А вот ещё одна важная новость.",
]


class TransitionPlanner:
    """Создаёт переходы между парами новостей."""

    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or create_llm_provider()

    async def plan(
        self,
        ordered: list[NewsItem],
        use_llm: bool = True,
    ) -> list[Transition]:
        """Возвращает список переходов (по одному между соседними новостями).

        Если use_llm=False или LLM недоступен — детерминированные шаблоны.
        """
        transitions: list[Transition] = []
        if len(ordered) < 2:
            return transitions

        if use_llm and getattr(__import__("config"), "TRANSITIONS_ENABLED", True):
            try:
                generated = await self._llm_transitions(ordered)
                if generated and len(generated) == len(ordered) - 1:
                    return generated
            except (LLMError, Exception) as exc:
                logger.warning("LLM-переходы не удались (%s), шаблоны", exc)

        for i in range(len(ordered) - 1):
            text = TEMPLATES[i % len(TEMPLATES)]
            transitions.append(Transition(from_id=ordered[i].id, to_id=ordered[i + 1].id, text=text))
        return transitions

    async def _llm_transitions(self, ordered: list[NewsItem]) -> list[Transition] | None:
        """PHASE 3: локальная LLM генерирует переходы по SUMMARY новостей."""
        import config
        from utils.json_utils import as_str_list, extract_json

        if not config.TRANSITIONS_ENABLED:
            return None

        pairs = []
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            pairs.append(f"{i + 1}: «{a.title}» -> «{b.title}» (summary A: {a.summary or a.edited_text[:80]})")

        prompt = (
            "Ты — редактор новостного шоу. Напиши {n} коротких переходов между парами новостей.\n"
            "Правила: 1) короткие (до 60 символов), 2) БЕЗ новых фактов и названий компаний, "
            "3) разговорный стиль, 4) не повторяй шаблоны.\n"
            "Примеры: «А теперь к следующей новости.», «Тем временем...», "
            "«Но на этом новости не заканчиваются.»\n"
            "Верни СТРОГО JSON: {{\"transitions\": [\"текст 1\", \"текст 2\", ...]}}\n\n"
            "Пары:\n{pairs}"
        )
        content = await self.provider.complete(prompt.format(n=len(pairs), pairs="\n".join(pairs)))
        data = extract_json(content)
        texts = as_str_list(data.get("transitions")) if isinstance(data, dict) else []
        if len(texts) != len(pairs):
            return None
        return [
            Transition(from_id=ordered[i].id, to_id=ordered[i + 1].id, text=texts[i].strip())
            for i in range(len(pairs))
        ]


__all__ = ["TransitionPlanner", "TEMPLATES"]