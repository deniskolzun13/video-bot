import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import config
from ai import create_llm_provider
from script import analyze_text, generate_script, plan_scenes
from storage import get_db, save_job_history
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
from utils.cleanup import cleanup_dir, remove_tree
from utils.errors import UserError, ValidationError
from video.selector import VideoSelector
from video_source import (
    PexelsProvider,
    SteamProvider,
    VideoSourceError,
    extract_game_name,
    prepare_clips,
    _prepare_steam_clips,
)
from video_render import render_video, validate_output

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


async def _build_provider(text: str, settings: dict | None = None):
    """Выбор источника видео: steam (игра из новости) / pexels (сток)."""
    source = (settings or {}).get("video_source") or config.VIDEO_SOURCE
    if source in ("steam", "auto"):
        game = await extract_game_name(text)
        if game:
            logger.info("Видео-источник: Steam, игра «%s»", game)
            return SteamProvider(game)
        if source == "steam":
            raise UserError("VIDEO_SOURCE=steam, но не удалось определить игру в тексте")
    logger.info("Видео-источник: Pexels")
    return PexelsProvider(config.PEXELS_API_KEY)


async def _select_clips(
    phrases: list[str],
    timings: list[tuple[float, float]],
    provider,
    work_dir: Path,
    scenes=None,
) -> list[tuple[Path, float, float]]:
    """Выбор клипов: Steam — нарезка трейлера (стабильная логика),
    Pexels — ранжированный выбор по сценам с защитой от повторов и fallback-фоном."""
    if isinstance(provider, SteamProvider):
        return await _prepare_steam_clips(phrases, timings, provider, work_dir)

    if scenes:
        selector = VideoSelector(provider, work_dir)
        return await selector.select(scenes, timings)

    # Старый путь (совместимость): глобальные ключевые слова
    return await prepare_clips(phrases, timings, provider, work_dir, None)


def _match_scenes_to_phrases(phrases: list[str], scenes) -> list:
    """Связывает сцены с фразами через map_scenes_to_phrases (по phrase_indexes)."""
    from script.scene_planner import Scene, map_scenes_to_phrases

    if not scenes:
        return []
    return map_scenes_to_phrases(phrases, [s if isinstance(s, Scene) else s for s in scenes])


async def process_text(
    text: str,
    work_dir: Path | str,
    notify: StatusCallback,
    task_id: str | None = None,
    job_id: str | None = None,
    user_id: int | None = None,
    settings: dict | None = None,
    cancel_token=None,
    render_semaphore=None,
    job_dir: Path | None = None,
) -> list[Path]:
    """Полный пайплайн v2.0:

    text → analysis → script → scenes → TTS → alignment → video selection
         → subtitles → render → validate → history → [mp4, ...]

    Возвращает список готовых mp4 (текст может быть разбит на несколько роликов).
    Никогда не поднимает исключения из-за отсутствия видео (fallback-фон).
    cancel_token — опциональный CancellationToken (проверяется на каждом этапе;
    ffmpeg-рендер при отмене завершается через terminate()/kill()).
    """
    if cancel_token:
        cancel_token.check()

    text = text.strip()
    if not text:
        raise UserError("Пустой текст")

    parts = split_for_videos(text)
    if len(parts) > config.MAX_PARTS:
        raise UserError(
            f"Текст слишком длинный: {len(parts)} частей (максимум {config.MAX_PARTS}). "
            f"Сократи до {config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS} символов."
        )

    # 1. Анализ текста (topic, keywords, visual_keywords)
    await notify("🧠 Анализирую текст…")
    if cancel_token:
        cancel_token.check()
    llm = create_llm_provider()
    analysis = await analyze_text(text, llm)
    keywords = analysis.keywords or []
    logger.info("Анализ: topic=%s, keywords=%s", analysis.topic, keywords[:4])

    # 2. Сценарий (опционально, config.SCRIPT_GENERATION=on)
    script_text = text
    if config.SCRIPT_GENERATION == "on":
        await notify("✍️ Создаю сценарий…")
        if cancel_token:
            cancel_token.check()
        script = await generate_script(text, analysis, llm)
        if script and script.full_text:
            script_text = script.full_text
            parts = split_for_videos(script_text)
            logger.info("Сценарий сгенерирован: %d слов", len(script_text.split()))

    provider = await _build_provider(text, settings)
    root = Path(work_dir)
    videos: list[Path] = []
    saved_job_meta = job_id

    try:
        for index, part in enumerate(parts):
            if cancel_token:
                cancel_token.check()

            # Уникальный task_id для изоляции параллельных задач
            part_task_id = task_id or uuid.uuid4().hex[:8]
            if job_dir is not None:
                wd = Path(job_dir) / f"part_{index}"
                sub_dirs = {
                    "tts": Path(job_dir) / "tts",
                    "video": Path(job_dir) / "video",
                    "subtitles": Path(job_dir) / "subtitles",
                }
                for d in sub_dirs.values():
                    d.mkdir(parents=True, exist_ok=True)
            else:
                wd = root / f"{part_task_id}_part_{index}"
                sub_dirs = {"tts": wd, "video": wd, "subtitles": wd}
            wd.mkdir(parents=True, exist_ok=True)

            # 3. Планирование сцен для этой части
            scenes = None
            if not isinstance(provider, SteamProvider):
                scene_plan = await plan_scenes(part, analysis, llm, config.SCENES_MAX)
                scenes = scene_plan.scenes

            await notify(f"🎙 Генерирую озвучку ({index + 1}/{len(parts)})…")
            if cancel_token:
                cancel_token.check()
            audio_path, duration = await synthesize(
                part, sub_dirs["tts"],
                voice=settings.get("voice") if settings else None,
                speed=settings.get("speed") if settings else None,
            )

            if duration > config.MAX_VIDEO_DURATION:
                raise UserError(
                    f"Озвучка длится {duration:.0f} с — больше лимита "
                    f"{config.MAX_VIDEO_DURATION:.0f} с. Сократи текст."
                )

            if duration > config.VIDEO_DURATION_WARN_THRESHOLD:
                await notify(
                    f"⚠️ Ролик получился длинным ({duration:.0f} сек). "
                    f"Для Shorts/Reels лучше 20–40 сек — рекомендуем сократить текст."
                )

            # 4. Тайминг: word-level (ASR) → whisper → пропорциональный
            await notify("⏱ Считаю тайминг…")
            if cancel_token:
                cancel_token.check()
            phrases = split_into_phrases(part)
            word_ts = await get_word_timestamps(audio_path, part)
            timings = await build_timings_word_level(phrases, word_ts) if word_ts else None
            if timings is None:
                timings = await build_timings_aligned(phrases, str(audio_path))
            if timings is None:
                timings = build_timings(phrases, duration)

            # Karaoke: word-тайминги по фразам
            word_timings_per_phrase = None
            if getattr(config, "SUB_KARAOKE", False) and word_ts:
                word_timings_per_phrase = words_to_phrase_timings(phrases, word_ts)

            # 5. Видео-подбор (ранжированный, с fallback-фоном)
            await notify("🎞 Подбираю видео…")
            if cancel_token:
                cancel_token.check()
            if scenes:
                scene_subset = _match_scenes_to_phrases(phrases, scenes)
            else:
                scene_subset = None

            try:
                clips = await _select_clips(phrases, timings, provider, sub_dirs["video"], scene_subset)
            except (VideoSourceError, ValueError) as exc:
                if isinstance(provider, SteamProvider) and ((settings or {}).get("video_source") or config.VIDEO_SOURCE) == "auto":
                    logger.warning("Steam не дал видео (%s), fallback на Pexels", exc)
                    await notify("🔄 Steam пуст — переключаюсь на сток…")
                    provider = PexelsProvider(config.PEXELS_API_KEY)
                    clips = await _select_clips(phrases, timings, provider, sub_dirs["video"], scene_subset)
                else:
                    raise

            # 6. Субтитры
            await notify("📝 Создаю субтитры…")
            if cancel_token:
                cancel_token.check()
            ass_path = generate_ass(
                phrases, timings, sub_dirs["subtitles"] / "subs.ass", keywords,
                word_timings_per_phrase if getattr(config, "SUB_KARAOKE", False) else None,
                settings.get("subtitle_style") if settings else None,
            )
            generate_srt(phrases, timings, sub_dirs["subtitles"] / "subs.srt")

            # 7. Рендер
            await notify("⚙️ Рендерю видео…")
            if cancel_token:
                cancel_token.check()
            video_format = (settings or {}).get("format") or "vertical"
            render_w, render_h = config.resolve_video_size(video_format)
            out_path = Path(config.OUTPUT_DIR) / f"video_{int(time.time())}_{index}.mp4"
            render_kwargs = dict(
                width=render_w, height=render_h, cancel_token=cancel_token
            )
            if render_semaphore is not None:
                async with render_semaphore:
                    await asyncio.to_thread(
                        render_video, clips, audio_path, ass_path, out_path, **render_kwargs
                    )
            else:
                await asyncio.to_thread(
                    render_video, clips, audio_path, ass_path, out_path, **render_kwargs
                )

            # 8. Валидация выхода
            check = validate_output(out_path, target_w=render_w, target_h=render_h)
            if not check["ok"]:
                logger.warning("Валидация не пройдена: %s", check["reasons"])
                raise ValidationError(
                    "Рендер завершился, но файл повреждён: " + ", ".join(check["reasons"])
                )
            logger.info("Валидация OK: %s (%.1fс, %s)", out_path.name,
                        check["duration"], check["resolution"])

            videos.append(out_path)
            cleanup_dir(wd, keep=("subs.ass", "subs.srt", "tts_audio.mp3"))
            if cancel_token:
                cancel_token.check()

        # 9. История
        if saved_job_meta:
            for v in videos:
                save_job_history(
                    get_db(), saved_job_meta, user_id or 0, text,
                    script=script_text if script_text != text else "",
                    status="completed", output_path=str(v), duration=check.get("duration", 0),
                )

        return videos
    except Exception:
        for v in videos:
            try:
                Path(v).unlink(missing_ok=True)
            except Exception:
                pass
        raise
    finally:
        try:
            remove_tree(root)
        except Exception:
            pass