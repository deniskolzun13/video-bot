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
)
from tts import get_word_timestamps, synthesize
from video_render import render_video
from video_source import PexelsProvider, SteamProvider, extract_game_name, prepare_clips

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
        ass_path = generate_ass(phrases, timings, wd / "subs.ass")
        generate_srt(phrases, timings, wd / "subs.srt")

        await notify("🎬 Подбираю и скачиваю видео-клипы…")
        try:
            clips = await prepare_clips(phrases, timings, provider, wd)
        except ValueError as exc:
            if isinstance(provider, SteamProvider) and config.VIDEO_SOURCE == "auto":
                logger.warning("Steam не дал видео (%s), fallback на Pexels", exc)
                provider = PexelsProvider(config.PEXELS_API_KEY)
                clips = await prepare_clips(phrases, timings, provider, wd)
            else:
                raise

        await notify("⚙️ Рендерю видео…")
        out_path = Path(config.OUTPUT_DIR) / f"video_{int(time.time())}_{index}.mp4"
        await asyncio.to_thread(render_video, clips, audio_path, ass_path, out_path)
        videos.append(out_path)

    return videos