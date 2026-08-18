import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import config
from subtitles import (
    build_timings,
    build_timings_aligned,
    build_timings_word_level,
    generate_ass,
    generate_srt,
    split_into_phrases,
    split_sentences,
    words_to_phrase_timings,
)
from tts import get_word_timestamps, synthesize
from video_render import render_video
from video_source import (
    PexelsProvider,
    SteamProvider,
    extract_game_name,
    extract_keywords,
    prepare_clips,
    translate_keywords,
)

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], Awaitable[None]]


def split_for_videos(text: str, limit: int = config.MAX_VIDEO_SYMBOLS) -> list[str]:
    """Разбивка длинного текста на несколько роликов по границам предложений."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        while len(sentence) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


async def _build_provider(text: str):
    """Выбор источника видео: steam (игра из новости) / pexels (сток)."""
    source = config.VIDEO_SOURCE
    if source in ("steam", "auto"):
        game = await extract_game_name(text)
        if game:
            logger.info("Видео-источник: Steam, игра «%s»", game)
            return SteamProvider(game)
        if source == "steam":
            raise ValueError("VIDEO_SOURCE=steam, но не удалось определить игру в тексте")
    logger.info("Видео-источник: Pexels")
    return PexelsProvider(config.PEXELS_API_KEY)


async def process_text(
    text: str,
    work_dir: Path | str,
    notify: StatusCallback,
    task_id: str | None = None,
) -> list[Path]:
    """Полный пайплайн: озвучка -> тайминг -> клипы -> рендер.
    Возвращает список готовых mp4 (текст может быть разбит на несколько роликов).
    task_id используется для изоляции временных файлов при параллельном запуске.
    """
    text = text.strip()
    if not text:
        raise ValueError("Пустой текст")

    parts = split_for_videos(text)
    if len(parts) > config.MAX_PARTS:
        raise ValueError(
            f"Текст слишком длинный: {len(parts)} частей (максимум {config.MAX_PARTS}). "
            f"Сократи до {config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS} символов."
        )

    provider = await _build_provider(text)

    # Extract global keywords once for the entire text (used for video clip selection)
    global_keywords = await extract_keywords(text)
    global_keywords = await translate_keywords(global_keywords)
    logger.info("Глобальные темы для всего текста: %s", global_keywords)

    root = Path(work_dir)
    videos: list[Path] = []
    for index, part in enumerate(parts):
        # Уникальный task_id для изоляции параллельных задач
        part_task_id = task_id or uuid.uuid4().hex[:8]
        wd = root / f"{part_task_id}_part_{index}"
        wd.mkdir(parents=True, exist_ok=True)

        await notify(f"🎧 Озвучиваю ({index + 1}/{len(parts)})…")
        audio_path, duration = await synthesize(part, wd)

        if duration > config.MAX_VIDEO_DURATION:
            raise ValueError(
                f"Озвучка длится {duration:.0f} с — больше лимита "
                f"{config.MAX_VIDEO_DURATION:.0f} с. Сократи текст."
            )

        # Warn if video is longer than optimal for engagement (Shorts/Reels)
        if duration > config.VIDEO_DURATION_WARN_THRESHOLD:
            await notify(
                f"⚠️ Ролик получился длинным ({duration:.0f} сек). "
                f"Для Shorts/Reels лучше 20–40 сек — рекомендуем сократить текст."
            )

        await notify("✂️ Разбиваю на фразы и считаю тайминг…")
        phrases = split_into_phrases(part)
        # 1. Word-level timestamps from Yandex ASR (most accurate)
        word_ts = await get_word_timestamps(audio_path, part)
        timings = await build_timings_word_level(phrases, word_ts) if word_ts else None
        # 2. Forced alignment via whisper-timestamped
        if timings is None:
            timings = await build_timings_aligned(phrases, str(audio_path))
        # 3. Proportional fallback
        if timings is None:
            timings = build_timings(phrases, duration)

        # For karaoke mode: convert word timestamps to per-phrase word timings
        word_timings_per_phrase = None
        if config.SUB_KARAOKE and word_ts:
            word_timings_per_phrase = words_to_phrase_timings(phrases, word_ts)
        ass_path = generate_ass(
            phrases, timings, wd / "subs.ass", global_keywords,
            word_timings_per_phrase if config.SUB_KARAOKE else None,
        )
        generate_srt(phrases, timings, wd / "subs.srt")

        await notify("🎬 Подбираю и скачиваю видео-клипы…")
        try:
            clips = await prepare_clips(phrases, timings, provider, wd, global_keywords)
        except ValueError as exc:
            if isinstance(provider, SteamProvider) and config.VIDEO_SOURCE == "auto":
                logger.warning("Steam не дал видео (%s), fallback на Pexels", exc)
                provider = PexelsProvider(config.PEXELS_API_KEY)
                clips = await prepare_clips(phrases, timings, provider, wd, global_keywords)
            else:
                raise

        await notify("⚙️ Рендерю видео…")
        out_path = Path(config.OUTPUT_DIR) / f"video_{int(time.time())}_{index}.mp4"
        await asyncio.to_thread(render_video, clips, audio_path, ass_path, out_path)
        videos.append(out_path)

    return videos