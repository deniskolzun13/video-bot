"""Парсер новостей: несколько новостей из текста/нескольких сообщений.

Поддерживает:
  - один текст с разделителями:
        === NEWS 1 ===
        текст...
        === NEWS 2 ===
        текст...
  - несколько отдельных текстов (список сообщений).
  - несколько абзацев (если разделителей нет — каждый непустой абзац = новость,
    но только если абзацев >= 2 и каждый достаточно длинный).
"""
import logging
import re


logger = logging.getLogger(__name__)

NEWS_DELIMITER_RE = re.compile(r"^[=\-*#]+\s*(?:NEWS\s*)?(\d+)\s*[=\-*#]+$", re.IGNORECASE)
# Также ловим "Новость 1:", "News 1 —" и т.п.
NEWS_HEADER_RE = re.compile(r"^\s*(?:новость|news)\s*(\d+)\s*[:\-–—\.]\s*$", re.IGNORECASE)


def parse_news_batch(text: str) -> list[str]:
    """Разбирает текст с разделителями '=== NEWS N ===' на список новостей.

    Возвращает список текстов новостей (в исходном порядке). Если текст
    не содержит разделителей — вернёт [text] (одна новость).
    """
    text = (text or "").strip()
    if not text:
        return []

    lines = text.splitlines()
    entries: list[tuple[int, str]] = []  # (index, text)
    current: list[str] = []

    def flush() -> None:
        if current:
            cleaned = "\n".join(current).strip()
            if cleaned:
                entries.append((len(entries), cleaned))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if NEWS_DELIMITER_RE.match(stripped) or NEWS_HEADER_RE.match(stripped):
            flush()
            continue
        current.append(line)
    flush()

    # Если разделителей не было (entries == 1) — проверяем: может, несколько
    # абзацев = несколько новостей? Нет: без разделителей считаем одной новостью,
    # чтобы не ломать обычный режим (пользователь пишет один текст).
    return [e for _, e in entries]


def split_news_from_messages(messages: list[str]) -> list[str]:
    """Несколько сообщений -> список новостей.

    Каждое сообщение — потенциально новость (если > N символов). Если сообщение
    содержит разделители === NEWS === — дополнительно разбивается.
    """
    result: list[str] = []
    for msg in messages:
        parsed = parse_news_batch(msg)
        for item in parsed:
            if len(item) >= 30:  # слишком короткое сообщение — пропускаем (шум)
                result.append(item)
    return result


def validate_batch(news: list[str]) -> list[str]:
    """Проверяет лимиты: количество и суммарную длину. Возвращает список ошибок."""
    import config

    errors: list[str] = []
    if not news:
        errors.append("Нет новостей для обработки")
        return errors
    if len(news) > config.MAX_NEWS_PER_BATCH:
        errors.append(
            f"Слишком много новостей: {len(news)} (лимит {config.MAX_NEWS_PER_BATCH})"
        )
    total = sum(len(n) for n in news)
    if total > config.MAX_TOTAL_BATCH_LENGTH:
        errors.append(
            f"Суммарная длина новостей {total} превышает лимит "
            f"{config.MAX_TOTAL_BATCH_LENGTH} символов"
        )
    for i, n in enumerate(news, 1):
        if len(n) > config.MAX_NEWS_TEXT_LENGTH:
            errors.append(
                f"Новость {i} слишком длинная: {len(n)} символов "
                f"(лимит {config.MAX_NEWS_TEXT_LENGTH})"
            )
    return errors


__all__ = ["parse_news_batch", "split_news_from_messages", "validate_batch"]