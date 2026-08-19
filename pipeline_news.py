"""Пайплайн выпуска новостей (разделы 9-20, 23-33): НЕСКОЛЬКО новостей -> ОДИН MP4.

Цепочка:
  несколько новостей
  -> NewsBatch (локальная LLM: редактирование, дедупликация, порядок)
  -> UnifiedScript (intro/news/transition/outro)
  -> локальный TTS (каждый сегмент)
  -> локальный ASR (whisper) + тайминг
  -> видео (local -> cache -> online)
  -> UnifiedTimeline
  -> ОДИН финальный FFmpeg render
  -> ONE mp4.
"""
import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Awaitable, Callable

import config
from ai import create_llm_provider
from news import (
    NewsBatch,
    NewsEditor,
    NewsItem,
    ScriptBuilder,
    SegmentAudio,
    TransitionPlanner,
    UnifiedScript,
    build_timeline,
    deduplicate,
    order_news,
    validate_batch,
)
from utils.errors import UserError
from video.local import LocalVideoProvider
from video_render_unified import render_unified_timeline

logger = logging.getLogger(__name__)

StatusCallback = Callable[[str], Awaitable[None]]


async def _notify(notify: StatusCallback, status: str, cancel_token) -> None:
    if cancel_token:
        cancel_token.check()
    if notify:
        await notify(status)


def _get_tts() -> Callable:
    """Возвращает функцию синтеза: локальный или облачный TTS."""
    if config.AI_MODE == "local":
        from tts_local import synthesize_local

        return synthesize_local
    from tts import synthesize

    return synthesize


def _get_video_provider(settings: dict | None = None):
    """Провайдер видео: local (data/media) или online (Pexels/Pixabay)."""
    source = (settings or {}).get("video_source") or config.VIDEO_SOURCE
    if source == "local":
        return LocalVideoProvider()
    if source == "online":
        from video_source import PexelsProvider

        return PexelsProvider(config.PEXELS_API_KEY)
    # auto: local -> cache -> online (кэш внутри download; здесь — локально сперва)
    if Path(config.LOCAL_MEDIA_DIR).exists():
        return LocalVideoProvider()
    from video_source import PexelsProvider

    return PexelsProvider(config.PEXELS_API_KEY)


async def _select_clip_for_item(
    item_text: str,
    keywords: list[str],
    provider,
    work_dir: Path,
    index: int,
    used_ids: set,
    duration: float,
):
    """Выбирает и скачивает 1 клип для сегмента. Возвращает (path, dur, off) или None."""
    from video_source import VideoSourceError

    dest = work_dir / f"clip_{index:03d}.mp4"
    queries = list(keywords or []) + [item_text[:60]]
    try:
        candidates = await provider.search(queries[0], per_page=5)
    except (VideoSourceError, ValueError, Exception) as exc:
        logger.warning("Поиск видео '%s' не удался (%s)", queries[0], exc)
        candidates = []

    best = None
    for cand in candidates:
        if cand.id not in used_ids:
            best = cand
            break
    if best is None and candidates:
        best = candidates[0]  # разрешаем повтор, лишь бы не упасть в фон
    if best is None:
        return None
    try:
        await provider.download(best, dest)
    except (ValueError, Exception) as exc:
        logger.warning("Скачивание клипа не удалось (%s)", exc)
        return None
    if not dest.exists() or dest.stat().st_size == 0:
        return None
    used_ids.add(best.id)
    return (dest, duration, 0.0)


async def _prepare_video_for_segment(
    item,
    keywords: list[str],
    provider,
    work_dir: Path,
    index: int,
    used_ids: set,
    work_root: Path,
) -> list:
    """Готовит клипы для сегмента (fallback: сгенерированный фон)."""
    from video.fallback import make_fallback_clip

    clip = await _select_clip_for_item(
        item.text or "",
        keywords,
        provider,
        work_dir,
        index,
        used_ids,
        max(item.duration, 2.0),
    )
    if clip:
        return [clip]
    # Фон
    fallback_dest = work_dir / f"bg_{index:03d}.mp4"
    try:
        make_fallback_clip(fallback_dest, max(item.duration, 2.0), index)
        if fallback_dest.exists():
            return [(fallback_dest, max(item.duration, 2.0), 0.0)]
    except Exception as exc:
        logger.warning("Fallback-фон не создан: %s", exc)
    return []


async def _build_segment_audio(
    script: UnifiedScript,
    job_dir: Path,
    cancel_token,
    notify: StatusCallback,
) -> dict[str, SegmentAudio]:
    """TTS для каждого блока сценария. Возвращает {block_key: SegmentAudio}."""
    tts_fn = _get_tts()
    tts_dir = job_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, SegmentAudio] = {}
    total = len(script.blocks)
    for idx, (block_type, news_id, text) in enumerate(script.blocks):
        key = _block_key(block_type, news_id)
        await _notify(notify, f"🎙 Озвучка {idx + 1}/{total}…", cancel_token)
        try:
            if asyncio.iscoroutinefunction(tts_fn):
                audio_path, duration = await tts_fn(text, tts_dir / key)
            else:
                audio_path, duration = await asyncio.to_thread(tts_fn, text, tts_dir / key)
        except Exception as exc:
            logger.error("TTS сегмента %s не удался: %s", key, exc)
            raise
        result[key] = SegmentAudio(block_key=key, text=text, audio_path=str(audio_path), duration=duration)
    return result


def _block_key(block_type: str, news_id: int | None) -> str:
    from news.timeline import _block_key as _bk

    return _bk(block_type, news_id)


async def process_news_batch(
    news_texts: list[str],
    work_dir: Path | str,
    notify: StatusCallback,
    job_id: str | None = None,
    user_id: int | None = None,
    settings: dict | None = None,
    cancel_token=None,
    render_semaphore=None,
    job_dir: Path | None = None,
) -> list[Path]:
    """Полный пайплайн выпуска новостей. Возвращает [одного mp4] (или пусто).

    news_texts — список текстов новостей (отдельные сообщения или с разделителями).
    """
    if cancel_token:
        cancel_token.check()
    news_texts = [t.strip() for t in news_texts if t and t.strip()]
    if not news_texts:
        raise UserError("Нет новостей для обработки")

    errors = validate_batch(news_texts)
    if errors:
        raise UserError("; ".join(errors))

    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    if job_dir is None:
        job_dir = root

    provider = create_llm_provider()

    # 1. NewsBatch: PHASE 1 — редактирование каждой новости
    await _notify(notify, f"📰 Получено {len(news_texts)} новостей.", cancel_token)
    await _notify(notify, "🧠 Анализирую…", cancel_token)
    editor = NewsEditor(provider)
    items: list[NewsItem] = []
    for i, text in enumerate(news_texts, 1):
        await _notify(notify, f"✍️ Редактирую {i}/{len(news_texts)}…", cancel_token)
        item = await editor.edit(i, text)
        items.append(item)
        # сохраняем оригинал
        (root / "news").mkdir(parents=True, exist_ok=True)
        (root / "news" / f"news_{i}.txt").write_text(text, encoding="utf-8")

    batch = NewsBatch(batch_id=job_id or uuid.uuid4().hex[:8], news=items)
    batch.order = [n.id for n in items]

    # 2. Дедупликация
    await _notify(notify, "🔄 Проверяю повторы…", cancel_token)
    unique, removed = deduplicate(items)
    if removed:
        await _notify(notify, f"🔁 Убрал повторы: {len(removed)}.", cancel_token)

    # 3. Порядок
    await _notify(notify, "🗂 Сортирую новости…", cancel_token)
    ordered = [n for n in order_news(unique) if n is not None]
    ordered_items = [next(n for n in unique if n.id == i) for i in ordered]
    batch.news = unique
    batch.order = ordered

    # 4. Переходы + сценарий
    await _notify(notify, "🎬 Планирую сцены…", cancel_token)
    planner = TransitionPlanner(provider)
    transitions = await planner.plan(ordered_items)
    builder = ScriptBuilder(provider)
    script = await builder.build(ordered_items, transitions)

    # Сохраняем batch + новости в SQLite (история, раздел 38)
    if not batch.batch_id:
        batch.batch_id = job_id or uuid.uuid4().hex[:8]
    from storage import get_db

    db = get_db()
    try:
        db.create_news_batch(batch.batch_id, user_id or 0, len(unique))
        for item in unique:
            db.save_news_item(batch.batch_id, item)
        db.update_news_batch(batch.batch_id, status="analyzing")
    except Exception as exc:
        logger.warning("Не сохранил news batch в БД: %s", exc)

    # 5. TTS всех сегментов
    seg_audio = await _build_segment_audio(script, Path(job_dir), cancel_token, notify)

    # 6. Тайминг и видео для сегментов
    video_provider = _get_video_provider(settings)
    video_dir = Path(job_dir) / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    used_ids: set[str] = set()

    # Построим таймлайн с учётом audio длительностей
    news_titles = {n.id: n.title for n in batch.news}
    timeline = build_timeline(script, seg_audio, news_titles=news_titles)

    # Word-level ASR (whisper singleton) для точных субтитров
    await _notify(notify, "🎧 Выравниваю субтитры…", cancel_token)
    from news.asr import align_timeline

    await align_timeline(timeline)

    # Сохраняем таймлайн в SQLite
    try:
        for item in timeline.items:
            db.save_timeline_item(batch.batch_id, item)
    except Exception as exc:
        logger.warning("Не сохранил timeline в БД: %s", exc)

    # Видео для каждого сегмента
    await _notify(notify, "🎞 Собираю видеоряд…", cancel_token)
    for idx, item in enumerate(timeline.items):
        if cancel_token:
            cancel_token.check()
        kw = []
        if item.news_id is not None:
            n = batch.news_by_id(item.news_id)
            if n:
                kw = n.keywords or []
        item.video_paths = await _prepare_video_for_segment(
            item, kw, video_provider, video_dir, idx, used_ids, Path(job_dir)
        )

    # 7. Субтитры
    await _notify(notify, "📝 Создаю субтитры…", cancel_token)
    subs_dir = Path(job_dir) / "subtitles"
    subs_dir.mkdir(parents=True, exist_ok=True)
    ass_path = subs_dir / "subs.ass"
    # Генерируется внутри render_unified_timeline (с абсолютными таймингами)

    # 8. Финальный рендер — ОДИН MP4
    await _notify(notify, "⚙️ Рендер…", cancel_token)
    video_format = (settings or {}).get("format") or "vertical"
    render_w, render_h = config.resolve_video_size(video_format)
    out_path = Path(config.OUTPUT_DIR) / f"news_{time.strftime('%Y%m%d')}_{int(time.time())}.mp4"

    async def _render():
        return await asyncio.to_thread(
            render_unified_timeline,
            timeline,
            ass_path,
            out_path,
            news_titles=news_titles,
            cancel_token=cancel_token,
            width=render_w,
            height=render_h,
        )

    if render_semaphore is not None:
        async with render_semaphore:
            await _render()
    else:
        await _render()

    # 9. Валидация + сохранение
    try:
        db.update_news_batch(
            batch.batch_id, status="completed", output_path=str(out_path),
            completed_at=time.time(),
        )
    except Exception as exc:
        logger.warning("Не обновил news batch: %s", exc)

    await _notify(notify, "✅ Готово.", cancel_token)
    return [out_path]


__all__ = ["process_news_batch"]