"""Видео-стадия: поиск, ранжирование, кэш, скачивание, fallback."""
from video.cache import cache_enabled, cache_key_for_clip, get_cached_clip, put_cached_clip
from video.downloader import download_clip
from video.fallback import make_fallback_clip
from video.ranking import ScoredClip, score_clip
from video.selector import VideoSelector

__all__ = [
    "cache_enabled",
    "cache_key_for_clip",
    "get_cached_clip",
    "put_cached_clip",
    "download_clip",
    "make_fallback_clip",
    "ScoredClip",
    "score_clip",
    "VideoSelector",
]