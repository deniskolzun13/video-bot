"""Единый интерфейс кэша. Единственная точка доступа к дисковому кэшу.

Правило архитектуры: ни один модуль (video_source, selector, pipeline) не
должен обращаться к cache.py / cache filesystem напрямую — только через
функции этого модуля.

Когда CACHE_ENABLED=false:
  - cache_get()  -> None
  - cache_put()  -> ничего не делает
  - cache_delete() -> безопасно работает (no-op)
  - cache_exists() -> False
  - cache_stats()  -> {enabled: False, ...}

Кэш-ключ учитывает provider + query + clip id (+ URL, если он влияет на
содержимое). SHA-256.
"""
import logging

import config

logger = logging.getLogger(__name__)

__all__ = [
    "cache_enabled",
    "cache_get",
    "cache_put",
    "cache_delete",
    "cache_exists",
    "cache_stats",
    "cache_key_for_clip",
    "clear_cache",
]


def cache_enabled() -> bool:
    """Отвечает на CACHE_ENABLED. Единственный источник истины."""
    return bool(getattr(config, "CACHE_ENABLED", True))


def _disabled_stats() -> dict:
    return {
        "enabled": False,
        "entries": 0,
        "total_size_mb": 0.0,
        "ttl_days": getattr(config, "CACHE_TTL_DAYS", 7),
        "max_size_mb": getattr(config, "MAX_CACHE_SIZE_MB", 500),
    }


def cache_key_for_clip(provider_name: str, clip: object | None = None,
                       clip_id: str = "", query: str = "", url: str = "") -> str:
    """SHA-256 ключ для клипа: провайдер + id + query + URL.

    URL включается в ключ, т.к. один и тот же id может отдавать разные
    варианты (Pexels пере-кодирует файлы). provider_name должен быть
    стабильным (например, имя класса без 'Provider').
    """
    from utils.hashing import stable_hash

    if clip is not None:
        clip_id = clip_id or getattr(clip, "id", "")
        query = query or getattr(clip, "query", "")
        url = url or getattr(clip, "url", "")
    return stable_hash(provider_name, clip_id, query, url)


def cache_get(key: str, provider: str) -> str | None:
    """Возвращает путь к кэшированному файлу или None (в т.ч. при CACHE_ENABLED=false)."""
    if not cache_enabled():
        return None
    from cache import get_cached

    cached = get_cached(key, provider)
    return str(cached) if cached else None


def cache_put(key: str, provider: str, src_path) -> None:
    """Сохраняет файл в кэш. No-op при CACHE_ENABLED=false."""
    if not cache_enabled():
        return
    from cache import put_to_cache

    try:
        put_to_cache(key, provider, src_path)
    except Exception as exc:
        logger.warning("Не удалось сохранить клип в кэш: %s", exc)


def cache_delete(key: str, provider: str) -> bool:
    """Удаляет запись из кэша. Безопасно работает даже при CACHE_ENABLED=false."""
    from cache import cache_delete as _delete

    try:
        return _delete(key, provider)
    except Exception as exc:
        logger.warning("Не удалось удалить из кэша: %s", exc)
        return False


def cache_exists(key: str, provider: str) -> bool:
    """Есть ли свежая запись. False при CACHE_ENABLED=false."""
    if not cache_enabled():
        return False
    return cache_get(key, provider) is not None


def cache_stats() -> dict:
    """Статистика кэша. При CACHE_ENABLED=false возвращает disabled state."""
    if not cache_enabled():
        return _disabled_stats()
    from cache import get_cache_stats

    stats = get_cache_stats()
    stats["enabled"] = True
    return stats


def clear_cache() -> int:
    """Полная очистка кэша. Возвращает количество удалённых файлов."""
    from cache import clear_cache as _clear

    return _clear()