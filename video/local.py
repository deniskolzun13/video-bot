"""Локальная библиотека видео (VIDEO_SOURCE=local, раздел 26).

data/media/<категория>/*.mp4
Категории: ai, technology, smartphones, computers, science, business, generic.

Поиск по filename/folder/metadata/keywords. Возвращает VideoClip с url=локальный
путь; download() просто копирует файл (без сети).
"""
import logging
import re
from pathlib import Path

import config
from video_source import VideoClip, VideoSourceProvider
from tts import probe_duration

logger = logging.getLogger(__name__)


class LocalVideoProvider(VideoSourceProvider):
    """Провайдер локальных видеофайлов."""

    def __init__(self, media_dir: str = "", categories: list[str] | None = None):
        self.media_dir = Path(media_dir or config.LOCAL_MEDIA_DIR)
        self.categories = categories or [
            "ai", "technology", "smartphones", "computers", "science", "business", "generic",
        ]

    def _all_files(self) -> list[Path]:
        if not self.media_dir.exists():
            return []
        exts = (".mp4", ".mov", ".mkv")
        files = []
        for cat in self.categories:
            cat_dir = self.media_dir / cat
            if cat_dir.is_dir():
                files.extend(p for p in cat_dir.iterdir() if p.suffix.lower() in exts)
        # также корень media_dir
        files.extend(p for p in self.media_dir.iterdir() if p.suffix.lower() in exts)
        return files

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower()))

    async def search(self, query: str, per_page: int = 5) -> list[VideoClip]:
        """Ищет локальные файлы по совпадению токенов с именем/категорией."""
        files = self._all_files()
        if not files:
            return []
        query_tokens = self._tokens(query)
        scored: list[tuple[int, Path, str]] = []
        for path in files:
            name = path.stem.replace("_", " ").replace("-", " ")
            folder = path.parent.name
            tokens = self._tokens(f"{name} {folder}")
            overlap = len(query_tokens & tokens) if query_tokens else 0
            if overlap or not query_tokens:
                scored.append((overlap, path, folder))
        # Сначала с совпадением, затем generic
        scored.sort(key=lambda s: (s[0] == 0, -s[0], s[1].name))

        clips: list[VideoClip] = []
        for _, path, folder in scored[:per_page]:
            clips.append(
                VideoClip(
                    id=f"local:{folder}:{path.name}",
                    url=str(path),
                    width=0,
                    height=0,
                    duration=0.0,
                    query=query,
                )
            )
        return clips

    async def search_photos(self, query: str, per_page: int = 5):
        """Локальные фото для Ken Burns fallback (data/media/*.jpg)."""
        photos = []
        for p in self.media_dir.rglob("*.jpg"):
            photos.append(photos_cls(
                id=f"local-photo:{p.name}",
                url=str(p),
                width=0,
                height=0,
                query=query,
            ))
        return photos[:per_page]

    def _cache_key(self, clip: VideoClip) -> str:
        return f"local:{clip.id}"

    async def download(self, clip: VideoClip, dest: Path) -> Path:
        """Копирует локальный файл в dest (кэш пропускаем — файл и так локальный)."""
        import shutil

        src = Path(clip.url)
        if not src.exists():
            raise ValueError(f"Локальное видео не найдено: {src}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest


def photos_cls(id, url, width, height, query):
    from video_source import VideoPhoto

    return VideoPhoto(id=id, url=url, width=width, height=height, query=query)


def probe_local_duration(path: Path) -> float:
    """Длительность локального видео через ffprobe (0 при ошибке)."""
    try:
        return probe_duration(path)
    except Exception:
        return 0.0


__all__ = ["LocalVideoProvider"]