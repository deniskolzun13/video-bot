"""Селектор видео: сцена -> визуальные ключи -> поиск -> ранжирование -> выбор.

- получает сцену (text, visual, keywords)
- делает несколько поисковых запросов
- собирает кандидатов, ранжирует их (video/ranking.py)
- выбирает лучший свободный (не в used_ids)
- при отсутствии видео — fallback на Pexels->Pixabay->сгенерированный фон
"""
import logging
from pathlib import Path

import config
from script.scene_planner import Scene
from video.downloader import download_clip
from video.fallback import make_fallback_clip
from video.ranking import score_clip
from video_source import (
    PixabayProvider,
    SteamProvider,
    VideoClip,
    VideoSourceError,
)

logger = logging.getLogger(__name__)


class VideoSelector:
    """Выбирает клипы для сцен с защитой от повторов и ранжированием."""

    def __init__(self, provider, work_dir: Path, used_ids: set[str] | None = None):
        self.provider = provider
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.used_ids = used_ids or set()

    def _dest(self, index: int) -> Path:
        return self.work_dir / f"clip_{index:03d}.mp4"

    async def _search_candidates(self, scene: Scene, per_page: int = 10) -> list[VideoClip]:
        """Ищет кандидатов по visual и keywords сцена, объединяя провайдеров."""
        queries = [scene.visual] if scene.visual else []
        queries += scene.keywords[:3]
        if not queries:
            queries = ["technology"]

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

        for query in queries:
            for cand in await _search(self.provider, query):
                if cand.id not in seen_ids:
                    candidates.append(cand)
                    seen_ids.add(cand.id)

            # Fallback-провайдеры: Pexels->Pixabay, если провайдер не Steam
            if not isinstance(self.provider, SteamProvider) and config.PIXABAY_API_KEY:
                pix = PixabayProvider(config.PIXABAY_API_KEY)
                for cand in await _search(pix, query):
                    if cand.id not in seen_ids:
                        candidates.append(cand)
                        seen_ids.add(cand.id)

        return candidates

    async def select(self, scenes: list[Scene], timings: list[tuple[float, float]]) -> list[tuple[Path, float, float]]:
        """Возвращает [(путь, длительность_сегмента, сдвиг)] для каждой сцены.

        Никогда не поднимает исключение из-за отсутствия видео:
        последняя надежда — сгенерированный фон.
        """
        result: list[tuple[Path, float, float]] = []
        for i, (scene, (start, end)) in enumerate(zip(scenes, timings)):
            need = max(end - start, 2.0)
            dest = self._dest(i)
            clip = await self._pick_best(scene, need)

            if clip is None:
                # Fallback: сгенерированный фон
                logger.warning("Сцена %d: видео не найдено, использую fallback-фон", i)
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
                logger.warning("Не удалось скачать клип %d (%s), fallback-фон", i, exc)
                make_fallback_clip(dest, need, i)
                result.append((dest, need, 0.0))

        return result

    async def _pick_best(self, scene: Scene, min_duration: float) -> VideoClip | None:
        """Ранжирует кандидатов и возвращает лучшего свободного."""
        candidates = await self._search_candidates(scene)
        if not candidates:
            return None
        scored = [
            score_clip(c, scene.visual, scene.keywords, self.used_ids, min_duration)
            for c in candidates
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        best = scored[0]
        if best.score < -1000:  # только дубликаты — значит все заняты
            return None
        logger.info("Лучший кандидат: id=%s score=%.1f (%s)",
                    best.clip.id, best.score, ", ".join(best.reasons) or "—")
        return best.clip


def clip_score(clip: VideoClip, scene: Scene) -> float:
    """Скоровый хелпер для логов (без дубликатов)."""
    scored = score_clip(clip, scene.visual, scene.keywords)
    return scored.score