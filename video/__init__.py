"""Видео-стадия: поиск, ранжирование, кэш, скачивание, fallback."""
from video.cache import (
    cache_delete,
    cache_enabled,
    cache_exists,
    cache_get,
    cache_key_for_clip,
    cache_put,
    cache_stats,
    clear_cache,
)
from video.downloader import download_clip
from video.fallback import make_fallback_clip
from video.ranking import ScoredClip, score_clip
from video.selector import VideoSelector

__all__ = [
    "cache_enabled",
    "cache_key_for_clip",
    "cache_get",
    "cache_put",
    "cache_delete",
    "cache_exists",
    "cache_stats",
    "clear_cache",
    "download_clip",
    "make_fallback_clip",
    "ScoredClip",
    "score_clip",
    "VideoSelector",
]