"""Тайминг субтитров: word-level (Yandex ASR) -> whisper-timestamped -> пропорциональный.

Формат word-level timestamps:
    [{"word": "OpenAI", "start": 0.00, "end": 0.45}, ...]

Сопоставление слов использует normalize_word() + умеренный fuzzy matching,
чтобы пережить расхождения ASR/whisper (GPT-5.6 -> gpt56, Open-AI -> openai).
Whisper-модель грузится лениво и один раз (потокобезопасный singleton);
транскрипция ограничена по времени ALIGNMENT_TIMEOUT_SECONDS.
"""
import asyncio
import logging
import re
import threading
import unicodedata

import config

logger = logging.getLogger(__name__)


def normalize_word(word: str) -> str:
    """Нормализует слово для сопоставления: lowercase, снятие пунктуации,
    дефисов/точек и юникод-вариаций (NFKC).

    "Hello!" -> "hello", "GPT-5.6" -> "gpt56", "Open-AI" -> "openai",
    "Café" -> "café" (акценты сохраняются).
    """
    text = unicodedata.normalize("NFKC", (word or "").lower())
    return re.sub(r"[^a-zа-яё0-9à-öø-ÿ]", "", text)


def fuzzy_word_match(a: str, b: str) -> bool:
    """Строгое равенство, либо равенство по префиксу с цифровым суффиксом
    (gpt == gpt4, gpt56 == gpt5) — частый случай версий моделей.

    Обычные слова (openai vs openaiv2) считаются НЕсовпадающими.
    """
    a, b = normalize_word(a), normalize_word(b)
    if not a or not b:
        return False
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    if not longer.startswith(shorter):
        return False
    suffix = longer[len(shorter):]
    if not suffix:
        return False
    # цифровой суффикс (gpt == gpt4) или одна буква версии (gpt4 == gpt4o)
    return suffix.isdigit() or len(suffix) == 1


def _words_from_phrase(phrase: str) -> list[str]:
    return [normalize_word(w) for w in re.findall(r"\S+", phrase) if normalize_word(w)]


# --- Whisper singleton (ленивый, потокобезопасный) ---
_WHISPER_MODEL = None
_WHISPER_MODEL_LOCK = threading.Lock()
_WHISPER_TS = None


def _get_whisper_model():
    """Возвращает лениво загруженную модель whisper-timestamped (один раз)."""
    global _WHISPER_MODEL, _WHISPER_TS
    if _WHISPER_MODEL is None:
        with _WHISPER_MODEL_LOCK:
            if _WHISPER_MODEL is None:
                import whisper_timestamped as whisper_ts

                _WHISPER_TS = whisper_ts
                _WHISPER_MODEL = whisper_ts.load_model("base", device="cpu")
    return _WHISPER_MODEL


async def _transcribe_with_timeout(audio_path: str, language: str):
    """Транскрипция whisper с таймаутом ALIGNMENT_TIMEOUT_SECONDS."""
    model = await asyncio.to_thread(_get_whisper_model)
    audio = await asyncio.to_thread(_WHISPER_TS.load_audio, audio_path)
    return await asyncio.wait_for(
        asyncio.to_thread(
            _WHISPER_TS.transcribe, model, audio, language=language, verbose=False
        ),
        timeout=config.ALIGNMENT_TIMEOUT_SECONDS,
    )


def _match_phrase_to_words(
    phrase_words: list[str],
    words: list[dict],
    start_index: int,
) -> tuple[float | None, float | None, int]:
    """Ищет последовательность phrase_words в words, начиная со start_index.

    Возвращает (start, end, индекс последнего совпавшего слова) или None.
    Сопоставление с перескоком: если слово не совпало, сдвигаем окно.
    """
    matched = 0
    start_t = None
    end_t = None
    last_idx = start_index
    i = start_index
    while i < len(words):
        w = words[i]
        if fuzzy_word_match(w.get("text", ""), phrase_words[matched]):
            if start_t is None:
                start_t = w.get("start")
            matched += 1
            end_t = w.get("end")
            last_idx = i
            i += 1
            if matched == len(phrase_words):
                return start_t, end_t, last_idx
        else:
            i += 1
    return None, None, last_idx


async def build_timings_aligned(
    phrases: list[str],
    audio_path: str,
    language: str = "ru",
) -> list[tuple[float, float]] | None:
    """Forced alignment через whisper-timestamped (CPU).

    Возвращает тайминги фраз, сопоставленные по словам.
    Falls back к None при ошибке/таймауте (вызывающий код перейдёт на
    пропорциональный).
    """
    try:
        await asyncio.to_thread(_get_whisper_model)
    except Exception as exc:
        logger.warning("whisper-timestamped недоступен (%s)", exc)
        return None

    try:
        logger.info("Запуск forced alignment (whisper-timestamped)...")
        result = await _transcribe_with_timeout(audio_path, language)

        words = []
        for segment in result.get("segments", []):
            for w in segment.get("words", []):
                words.append({
                    "text": w["text"].strip(),
                    "start": w["start"],
                    "end": w["end"],
                })

        if not words:
            logger.warning("Whisper не вернул слова")
            return None

        timings = []
        word_idx = 0
        for phrase in phrases:
            phrase_words = _words_from_phrase(phrase)
            if not phrase_words:
                timings.append((0.0, 0.0))
                continue

            start_t, end_t, word_idx = _match_phrase_to_words(
                phrase_words, words, word_idx
            )
            if start_t is None or end_t is None:
                logger.debug("Не удалось выровнять фразу: %s", phrase[:40])
                return None

            timings.append((start_t, end_t))

        logger.info("Forced alignment успешен: %d фраз", len(timings))
        return timings

    except asyncio.TimeoutError:
        logger.warning("Forced alignment превысил таймаут %.0f c",
                       config.ALIGNMENT_TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        logger.warning("Forced alignment упал (%s)", exc)
        return None


async def build_timings_word_level(
    phrases: list[str],
    word_timestamps: list[dict],
) -> list[tuple[float, float]] | None:
    """Тайминги фраз по word-level timestamps от Yandex ASR."""
    if not word_timestamps:
        return None

    try:
        timings = []
        word_idx = 0
        for phrase in phrases:
            phrase_words = _words_from_phrase(phrase)
            if not phrase_words:
                timings.append((0.0, 0.0))
                continue

            start_t = None
            end_t = None
            i = word_idx
            matched = 0
            while i < len(word_timestamps):
                w = word_timestamps[i]
                if fuzzy_word_match(w.get("word", ""), phrase_words[matched]):
                    if start_t is None:
                        start_t = w.get("start")
                    matched += 1
                    end_t = w.get("end")
                    i += 1
                    if matched == len(phrase_words):
                        break
                else:
                    i += 1

            if start_t is None or end_t is None:
                return None

            timings.append((start_t, end_t))
            word_idx = i
        logger.info("Word-level тайминг успешен: %d фраз", len(timings))
        return timings

    except Exception as exc:
        logger.warning("Word-level тайминг упал (%s)", exc)
        return None


def words_to_phrase_timings(
    phrases: list[str],
    word_timestamps: list[dict],
) -> list[list[tuple[str, float, float]]] | None:
    """Разбивает word-level timestamps на фразы для karaoke."""
    if not word_timestamps:
        return None

    try:
        result: list[list[tuple[str, float, float]]] = []
        word_idx = 0
        for phrase in phrases:
            phrase_words = _words_from_phrase(phrase)
            if not phrase_words:
                result.append([])
                continue

            phrase_word_times: list[tuple[str, float, float]] = []
            matched = 0
            i = word_idx

            while i < len(word_timestamps) and matched < len(phrase_words):
                w = word_timestamps[i]
                if fuzzy_word_match(w.get("word", ""), phrase_words[matched]):
                    phrase_word_times.append((w["word"], w["start"], w["end"]))
                    matched += 1
                i += 1

            if matched != len(phrase_words):
                return None

            result.append(phrase_word_times)
            word_idx = i

        return result
    except Exception as exc:
        logger.warning("words_to_phrase_timings упал (%s)", exc)
        return None


def build_timings(phrases: list[str], total_duration: float) -> list[tuple[float, float]]:
    """Пропорциональный тайминг по длине текста (фоллбэк)."""
    total_chars = sum(len(p) for p in phrases) or 1
    timings: list[tuple[float, float]] = []
    t = 0.0
    for phrase in phrases:
        dur = total_duration * len(phrase) / total_chars
        timings.append((t, t + dur))
        t += dur
    return timings