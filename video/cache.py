"""Кэш скачанных клипов на основе SHA-256 ключей.

Расширяет существующий cache.py (дисковый TTL/LRU кэш) и добавляет
контент-кэш: если конфиг CACHE_ENABLED=false — кэш полностью отключён.
"""
import logging

import config
from cache import get_cached, put_to_cache  # существующий кэш клипов
from utils.hashing import stable_hash

logger = logging.getLogger(__name__)

__all__ = ["cache_key_for_clip", "get_cached_clip", "put_cached_clip", "cache_enabled"]


def cache_enabled() -> bool:
    return getattr(config, "CACHE_ENABLED", True)


def cache_key_for_clip(provider_name: str, query: str, clip_id: str) -> str:
    """SHA-256 ключ для клипа: провайдер + запрос + id клипа."""
    return stable_hash(provider_name, query, clip_id)


def get_cached_clip(provider_name: str, key: str) -> str | None:
    """Возвращает путь к кэшированному клипу или None."""
    if not cache_enabled():
        return None
    cached = get_cached(key, provider_name)
    return str(cached) if cached else None


def put_cached_clip(provider_name: str, key: str, src_path) -> None:
    if not cache_enabled():
        return
    try:
        put_to_cache(key, provider_name, src_path)
    except Exception as exc:
        logger.warning("Не удалось сохранить клип в кэш: %s", exc)