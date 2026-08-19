"""Генератор сценария для вертикального видео.

Вход: исходная новость + Analysis.
Выход: Script (hook, body, ending, full_text).

Правила: короткие предложения, без выдуманных фактов, без повторов,
текст пригоден для TTS. Если пользователь прислал готовый сценарий —
не переписываем (контролируется config.SCRIPT_GENERATION=on/off).
"""
import logging
import re
from dataclasses import dataclass

from ai import LLMError, LLMProvider, create_llm_provider
from script.analyzer import Analysis

logger = logging.getLogger(__name__)

PROMPT_SCRIPT = (
    "Ты — автор сценариев для вертикальных видео (Shorts/Reels/TikTok). "
    "На основе новости и её анализа напиши сценарий озвучки.\n\n"
    "Строгие правила:\n"
    "1. НЕ выдумывай факты, не добавляй неподтверждённые сведения.\n"
    "2. Короткие предложения (до 60 символов), разговорный стиль.\n"
    "3. Хук (первая фраза) — короткий, цепляющий.\n"
    "4. Не повторяй одну мысль дважды.\n"
    "5. Объём — до 1000 символов суммарно.\n"
    "6. Верни СТРОГО один JSON без пояснений:\n"
    '{"hook": "...", "body": "...", "ending": "..."}\n\n'
    "Анализ новости:\n{analysis}\n\nНовость:\n{text}"
)


@dataclass
class Script:
    hook: str = ""
    body: str = ""
    ending: str = ""
    full_text: str = ""

    def to_dict(self) -> dict:
        return {
            "hook": self.hook,
            "body": self.body,
            "ending": self.ending,
            "full_text": self.full_text,
        }


class ScriptGenerator:
    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or create_llm_provider()

    async def generate(self, text: str, analysis: Analysis) -> Script | None:
        """Возвращает Script или None (если LLM недоступен/невалиден)."""
        try:
            content = await self.provider.complete(
                PROMPT_SCRIPT.format(text=text[:2000], analysis=analysis.to_dict())
            )
        except (LLMError, Exception) as exc:
            logger.warning("Генерация сценария не удалась (%s)", exc)
            return None

        data = self._extract_json(content)
        if not data:
            return None
        hook = (data.get("hook") or "").strip()
        body = (data.get("body") or "").strip()
        ending = (data.get("ending") or "").strip()
        parts = [p for p in (hook, body, ending) if p]
        if not parts:
            return None
        return Script(hook=hook, body=body, ending=ending,
                      full_text=" ".join(parts))

    @staticmethod
    def _extract_json(content: str) -> dict | None:
        if not content:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            import json
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None


async def generate_script(
    text: str,
    analysis: Analysis,
    provider: LLMProvider | None = None,
) -> Script | None:
    """Обёртка: генерирует сценарий. При ошибке возвращает None —
    пайплайн продолжит работу с исходным текстом."""
    return await ScriptGenerator(provider).generate(text, analysis)