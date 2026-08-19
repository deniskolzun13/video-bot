"""Построение UnifiedScript: INTRO + NEWS + TRANSITION + ... + OUTRO.

Каждый блок знает news_id (для субтитров/таймлайна/отдельного audio).
INTRO/OUTRO генерируются локальной LLM (компактный промпт по summaries),
при недоступности — шаблоны. Переходы — из TransitionPlanner.
"""
import logging

import config
from ai import LLMError, LLMProvider, create_llm_provider
from news.models import NewsItem, UnifiedScript
from utils.json_utils import as_str, extract_json

logger = logging.getLogger(__name__)

INTRO_TEMPLATE = "Привет! Собрал для тебя главные новости из мира технологий. Поехали!"
OUTRO_TEMPLATE = "На сегодня всё. Ставь лайк, если было полезно, и подписывайся — до встречи!"


class ScriptBuilder:
    """Собирает UnifiedScript из отредактированных новостей и переходов."""

    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or create_llm_provider()

    async def build(
        self,
        ordered: list[NewsItem],
        transitions: list,
        use_llm: bool = True,
    ) -> UnifiedScript:
        """Строит сценарий. Внутри каждого блока: (type, news_id, text).

        transition между новостью i и i+1 берётся по from_id/to_id.
        """
        intro = ""
        outro = ""
        if use_llm:
            intro = await self._generate_bookend(ordered, "intro")
            outro = await self._generate_bookend(ordered, "outro")
        if not intro:
            intro = INTRO_TEMPLATE if config.INTRO_ENABLED else ""
        if not outro:
            outro = OUTRO_TEMPLATE if config.OUTRO_ENABLED else ""

        blocks: list[tuple[str, int | None, str]] = []
        if intro:
            blocks.append(("intro", None, intro))

        trans_by_from = {t.from_id: t for t in transitions}
        for i, item in enumerate(ordered):
            blocks.append(("news", item.id, item.edited_text or item.original_text))
            if i < len(ordered) - 1:
                transition = trans_by_from.get(item.id)
                if transition and config.TRANSITIONS_ENABLED:
                    blocks.append(("transition", item.id, transition.text))

        if outro:
            blocks.append(("outro", None, outro))

        return UnifiedScript(intro=intro, outro=outro, blocks=blocks)

    async def _generate_bookend(self, ordered: list[NewsItem], kind: str) -> str:
        """Генерирует intro/outro через локальную LLM (компактные summaries)."""
        summaries = "\n".join(
            f"{i + 1}. {n.title}: {n.summary or n.edited_text[:80]}" for i, n in enumerate(ordered)
        )
        if kind == "intro":
            template = (
                "Ты — ведущий новостного шоу. Напиши короткое вступление (до 120 символов) "
                "к выпуску из новостей. Стиль — энергичный, разговорный.\n"
                "Правила: НЕ называй конкретные компании/события (они пойдут дальше), "
                "НЕ выдумывай факты.\n"
                "Верни СТРОГО JSON: {{\"text\": \"...\"}}\n\nНовости:\n{summaries}"
            )
        else:
            template = (
                "Ты — ведущий новостного шоу. Напиши короткое завершение (до 120 символов) "
                "для выпуска новостей. Стиль — энергичный, разговорный.\n"
                "Правила: НЕ выдумывай факты, без новых имён/компаний.\n"
                "Верни СТРОГО JSON: {{\"text\": \"...\"}}\n\nНовости:\n{summaries}"
            )
        try:
            content = await self.provider.complete(template.format(summaries=summaries[:1500]))
            data = extract_json(content)
            return as_str(data.get("text")) if isinstance(data, dict) else ""
        except (LLMError, Exception) as exc:
            logger.warning("LLM-%s не удался (%s), шаблон", kind, exc)
            return ""


__all__ = ["ScriptBuilder", "INTRO_TEMPLATE", "OUTRO_TEMPLATE"]