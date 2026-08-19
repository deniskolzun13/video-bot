"""Тайминг субтитров: word-level (Yandex ASR) -> whisper-timestamped -> пропорциональный.

Формат word-level timestamps:
    [{"word": "OpenAI", "start": 0.00, "end": 0.45}, ...]
"""
import logging
import re

logger = logging.getLogger(__name__)


async def build_timings_aligned(
    phrases: list[str],
    audio_path: str,
    language: str = "ru",
) -> list[tuple[float, float]] | None:
    """
    Forced alignment через whisper-timestamped (CPU).
    Возвращает тайминги фраз, сопоставленные по словам.
    Falls back к None при ошибке (вызывающий код перейдёт на пропорциональный).
    """
    try:
        import whisper_timestamped as whisper_ts
    except Exception as exc:
        logger.warning("whisper-timestamped недоступен (%s)", exc)
        return None

    try:
        logger.info("Запуск forced alignment (whisper-timestamped)...")
        model = whisper_ts.load_model("base", device="cpu")
        audio = whisper_ts.load_audio(audio_path)
        result = whisper_ts.transcribe(model, audio, language=language, verbose=False)

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
            phrase_words = re.findall(r"\b\w+\b", phrase.lower())
            if not phrase_words:
                timings.append((0.0, 0.0))
                continue

            matched = 0
            start_t = None
            end_t = None
            for i, w in enumerate(words[word_idx:], start=word_idx):
                if w["text"].lower() == phrase_words[matched]:
                    if start_t is None:
                        start_t = w["start"]
                    matched += 1
                    end_t = w["end"]
                    if matched == len(phrase_words):
                        word_idx = i + 1
                        break
                else:
                    matched = 0
                    start_t = None

            if start_t is None or end_t is None:
                logger.debug("Не удалось выровнять фразу: %s", phrase)
                return None

            timings.append((start_t, end_t))

        logger.info("Forced alignment успешен: %d фраз", len(timings))
        return timings

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
            phrase_words = re.findall(r"\b\w+\b", phrase.lower())
            if not phrase_words:
                timings.append((0.0, 0.0))
                continue

            matched = 0
            start_t = None
            end_t = None
            i = word_idx
            while i < len(word_timestamps):
                w = word_timestamps[i]
                if w["word"].lower() == phrase_words[matched]:
                    if start_t is None:
                        start_t = w["start"]
                    matched += 1
                    end_t = w["end"]
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
            phrase_words = re.findall(r"\b\w+\b", phrase.lower())
            if not phrase_words:
                result.append([])
                continue

            phrase_word_times: list[tuple[str, float, float]] = []
            matched = 0
            i = word_idx

            while i < len(word_timestamps) and matched < len(phrase_words):
                w = word_timestamps[i]
                if w["word"].lower() == phrase_words[matched]:
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