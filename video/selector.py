"""Селектор видео: сцена -> визуальные ключи -> поиск -> ранжирование -> выбор.

- получает сцену (visual, keywords, phrase_indexes)
- делает несколько поисковых запросов (visual + keywords + fallback query)
- собирает кандидатов, ранжирует их (video/ranking.py, прозрачные веса)
- выбирает лучший свободный (не в used_ids); если свободных нет —
  разрешает повтор лучшего из использованных (дубликат-защита с fallback)
- при отсутствии видео вообще — fallback на сгенерированный фон

Порядок провайдеров: Pexels -> Pixabay -> fallback query -> background.
"""
import logging
from pathlib import Path

import config
from script.scene_planner import Scene
from video.downloader import download_clip
from video.fallback import make_fallback_clip, make_photo_clip
from video.ranking import score_clip
from video_source import (
    PixabayProvider,
    SteamProvider,
    VideoClip,
    VideoPhoto,
    VideoSourceError,
)

logger = logging.getLogger(__name__)

# Фоллбэк-запросы (обобщённые темы), когда visual не дал результатов
FALLBACK_QUERIES = ["technology", "abstract", "office", "city", "nature"]


class VideoSelector:
    """Выбирает клипы для сцен с защитой от повторов и ранжированием."""

    def __init__(self, provider, work_dir: Path, used_ids: set[str] | None = None):
        self.provider = provider
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.used_ids = used_ids or set()

    def _dest(self, index: int) -> Path:
        return self.work_dir / f"clip_{index:03d}.mp4"

    def _fallback_query(self, scene: Scene) -> str:
        """Обобщённый запрос: последний keyword сцены, иначе — из глобального пула."""
        if scene.keywords:
            return scene.keywords[-1]
        for q in FALLBACK_QUERIES:
            if q not in (scene.visual or "").lower():
                return q
        return FALLBACK_QUERIES[0]

    async def _search_candidates(self, scene: Scene, per_page: int = 10) -> list[VideoClip]:
        """Ищет кандидатов по visual/keywords/fallback-запросу у провайдеров.

        Pexels -> Pixabay (если провайдер не Steam и задан ключ).
        Возвращает уникальные клипы.
        """
        queries: list[str] = []
        if scene.visual:
            queries.append(scene.visual)
        queries += scene.keywords[:3]
        queries.append(self._fallback_query(scene))
        # Убираем дубли запросов, сохраняя порядок
        seen_q: set[str] = set()
        unique_queries: list[str] = []
        for q in queries:
            if q not in seen_q:
                seen_q.add(q)
                unique_queries.append(q)

        candidates: list[VideoClip] = []
        seen_ids: set[str] = set()

        async def _search(provider, query: str) -> list[VideoClip]:
            try:
                found = await provider.search(query, per_page=per_page)
                return [c for c in found if c.id not in seen_ids]
            except (VideoSourceError, ValueError, Exception) as exc:
                logger.warning("Поиск '%s' у %s не удался: %s",
                               query, provider.__class__.__name__, exc)
                return []

        for query in unique_queries:
            if len(candidates) >= config.MIN_CLIPS_PER_PHRASE:
                break
            for cand in await _search(self.provider, query):
                if cand.id not in seen_ids:
                    candidates.append(cand)
                    seen_ids.add(cand.id)

            # Fallback-провайдеры: Pexels->Pixabay, если провайдер не Steam
            if len(candidates) < config.MIN_CLIPS_PER_PHRASE:
                if not isinstance(self.provider, SteamProvider) and config.PIXABAY_API_KEY:
                    pix = PixabayProvider(config.PIXABAY_API_KEY)
                    for cand in await _search(pix, query):
                        if cand.id not in seen_ids:
                            candidates.append(cand)
                            seen_ids.add(cand.id)

        logger.debug("Сцена '%s': кандидатов %d (минимум %d)",
                     scene.visual[:30], len(candidates), config.MIN_CLIPS_PER_PHRASE)
        return candidates

    async def select(self, scenes: list[Scene], timings: list[tuple[float, float]]) -> list[tuple[Path, float, float]]:
        """Возвращает [(путь, длительность_сегмента, сдвиг)] для каждой сцены.

        Никогда не поднимает исключение из-за отсутствия видео:
        видео -> стоковое фото (Ken Burns, если IMAGE_FALLBACK=true) -> фон.
        """
        result: list[tuple[Path, float, float]] = []
        for i, (scene, (start, end)) in enumerate(zip(scenes, timings)):
            need = max(end - start, 2.0)
            dest = self._dest(i)
            clip = await self._pick_best(scene, need)

            if clip is None:
                if getattr(config, "IMAGE_FALLBACK", False):
                    made = await self._photo_fallback(scene, dest, need)
                    if made:
                        result.append((dest, need, 0.0))
                        continue
                # Последняя надежда: сгенерированный фон
                logger.warning("Сцена %d: видео и фото не найдены, использую фон", i)
                make_fallback_clip(dest, need, i)
                result.append((dest, need, 0.0))
                continue

            try:
                await download_clip(self.provider, clip, dest)
                logger.info("Клип %d/%d: query=%s id=%s score=%.1f",
                            i + 1, len(scenes), clip.query, clip.id, clip_score(clip, scene))
                result.append((dest, need, 0.0))
                self.used_ids.add(clip.id)
            except ValueError as exc:
                logger.warning("Не удалось скачать клип %d (%s), фото/фон", i, exc)
                if getattr(config, "IMAGE_FALLBACK", False):
                    made = await self._photo_fallback(scene, dest, need)
                    if made:
                        result.append((dest, need, 0.0))
                        continue
                make_fallback_clip(dest, need, i)
                result.append((dest, need, 0.0))

        return result

    async def _photo_fallback(self, scene: Scene, dest: Path, need: float) -> bool:
        """Ken Burns по стоковому фото. True — если клип создан."""
        photo = await self._pick_photo(scene)
        if photo is None:
            return False
        try:
            make_photo_clip(dest, photo.url, need)
            logger.info("Ken Burns fallback для сцены '%s' (фото id=%s)",
                        scene.visual[:30], photo.id)
            return True
        except Exception as exc:
            logger.warning("Ken Burns fallback не удался (%s), фон", exc)
            return False

    async def _pick_photo(self, scene: Scene) -> VideoPhoto | None:
        """Ищет вертикальное фото: Pexels -> Pixabay."""
        query = scene.visual or (scene.keywords[0] if scene.keywords else None)
        if not query:
            return None
        providers = [self.provider]
        if not isinstance(self.provider, SteamProvider) and config.PIXABAY_API_KEY:
            providers.append(PixabayProvider(config.PIXABAY_API_KEY))
        for provider in providers:
            search = getattr(provider, "search_photos", None)
            if search is None:
                continue
            try:
                photos = await search(query, per_page=8)
                if photos:
                    # Отдаём первое вертикальное/самое близкое к 9:16
                    photos.sort(
                        key=lambda p: abs((p.height / max(p.width, 1)) - 16 / 9)
                        if p.height >= p.width else 1e9
                    )
                    return photos[0]
            except (VideoSourceError, ValueError, Exception) as exc:
                logger.warning("Поиск фото '%s' у %s не удался: %s",
                               query, provider.__class__.__name__, exc)
        return None

    async def _pick_best(self, scene: Scene, min_duration: float) -> VideoClip | None:
        """Ранжирует кандидатов и возвращает лучшего.

        Если все кандидаты уже использованы и есть хотя бы один свободный —
        выбираем свободный. Если свободных нет — разрешаем повтор лучшего
        (дубликат-защита с fallback), чтобы не упасть в фон.
        """
        candidates = await self._search_candidates(scene)
        if not candidates:
            return None

        scored = [
            score_clip(c, scene.visual, scene.keywords, self.used_ids, min_duration)
            for c in candidates
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        best = scored[0]

        # Есть свободный кандидат — берём его
        if best.score >= 0:
            logger.info("Лучший кандидат: id=%s score=%.3f",
                        best.clip.id, best.score)
            return best.clip

        # Все использованы: разрешаем повтор лучшего, если есть хоть какой-то
        # кандидат (не пустой список) — иначе None (фон)
        all_used = [s for s in scored if s.clip.id in self.used_ids]
        if all_used:
            allowed = [s for s in scored]
            allowed.sort(key=lambda s: s.score, reverse=True)
            top = allowed[0]
            logger.info("Все клипы сцены использованы — повтор лучшего id=%s", top.clip.id)
            return top.clip

        return None


def clip_score(clip: VideoClip, scene: Scene) -> float:
    """Скоровый хелпер для логов (без дубликатов)."""
    scored = score_clip(clip, scene.visual, scene.keywords)
    return scored.score