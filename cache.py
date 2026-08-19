"""Локальный дисковый кэш для скачанных клипов и Steam-трейлеров.

Безопасен при конкурентном доступе: метаданные пишутся атомарно
(temp-файл -> fsync -> os.replace) под threading.Lock.
Все внешние модули обращаются сюда только через video/cache.py.
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

CACHE_DIR = Path(getattr(config, "CACHE_DIR", "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = getattr(config, "CACHE_TTL_DAYS", 7) * 86400  # seconds
MAX_CACHE_SIZE_MB = getattr(config, "MAX_CACHE_SIZE_MB", 500)  # MB

META_FILE = CACHE_DIR / "cache_meta.json"

_META_LOCK = threading.Lock()


def _load_meta() -> dict:
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(meta: dict) -> None:
    """Атомарная запись метаданных: temp -> flush -> fsync -> os.replace."""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(meta, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, META_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:
        logger.warning("Не удалось сохранить метаданные кэша: %s", exc)


def _cache_key(key: str, provider: str) -> str:
    """Нормализованный ключ кэша."""
    import hashlib
    raw = f"{provider}:{key.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _cache_path(key: str, provider: str) -> Path:
    ck = _cache_key(key, provider)
    return CACHE_DIR / f"{provider}_{ck}.mp4"


def get_cached(key: str, provider: str):
    """Возвращает путь к кэшированному файлу, если он свежий."""
    ck = _cache_key(key, provider)
    path = _cache_path(key, provider)

    if not path.exists():
        return None

    with _META_LOCK:
        meta = _load_meta()
        entry = meta.get(ck)
        if not entry:
            return None

        if time.time() - entry["timestamp"] > CACHE_TTL:
            # Устарел
            try:
                path.unlink()
            except Exception:
                pass
            meta.pop(ck, None)
            _save_meta(meta)
            return None

        # Обновляем время последнего доступа (LRU)
        entry["last_access"] = time.time()
        meta[ck] = entry
        _save_meta(meta)

    return path


def put_to_cache(key: str, provider: str, src_path: Path) -> Path:
    """Копирует файл в кэш и возвращает путь в кэше."""
    src_path = Path(src_path)
    dest = _cache_path(key, provider)
    try:
        # Копируем во временный файл, затем атомарно переименовываем,
        # чтобы конкурентная запись не дала битый mp4.
        fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".mp4.tmp")
        os.close(fd)
        tmp = Path(tmp)
        shutil.copy2(src_path, tmp)
        os.replace(tmp, dest)
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError(f"Не удалось сохранить в кэш: {exc}")

    with _META_LOCK:
        meta = _load_meta()
        ck = _cache_key(key, provider)
        now = time.time()
        try:
            size = dest.stat().st_size
        except OSError:
            size = 0
        meta[ck] = {
            "key": key,
            "provider": provider,
            "timestamp": now,
            "last_access": now,
            "size": size,
        }
        _save_meta(meta)

    _enforce_size_limit()
    return CACHE_DIR / f"{provider}_{_cache_key(key, provider)}.mp4"


def cache_exists(key: str, provider: str) -> bool:
    """Есть ли свежая запись в кэше (файл на диске)."""
    return get_cached(key, provider) is not None


def cache_delete(key: str, provider: str) -> bool:
    """Удаляет запись из кэша. Возвращает True, если что-то удалено."""
    ck = _cache_key(key, provider)
    path = _cache_path(key, provider)
    removed = False
    try:
        if path.exists():
            path.unlink()
            removed = True
    except Exception:
        pass
    with _META_LOCK:
        meta = _load_meta()
        if ck in meta:
            meta.pop(ck, None)
            _save_meta(meta)
            removed = True
    return removed


def _enforce_size_limit() -> None:
    """Удаляет старые записи (LRU) если кэш превышает лимит."""
    with _META_LOCK:
        meta = _load_meta()
        total_size = sum(entry.get("size", 0) for entry in meta.values())
        limit = MAX_CACHE_SIZE_MB * 1024 * 1024

        if total_size <= limit:
            return

        # Сортируем по last_access (старые сначала)
        entries = sorted(meta.items(), key=lambda x: x[1].get("last_access", 0))

        for ck, entry in entries:
            if total_size <= limit:
                break
            path = CACHE_DIR / f"{entry['provider']}_{ck}.mp4"
            try:
                if path.exists():
                    path.unlink()
                    total_size -= entry.get("size", 0)
            except Exception:
                pass
            meta.pop(ck, None)

        _save_meta(meta)


def clear_cache() -> int:
    """Полная очистка кэша. Возвращает количество удалённых файлов."""
    with _META_LOCK:
        count = 0
        for f in CACHE_DIR.glob("*.mp4"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        for f in CACHE_DIR.glob("*.tmp"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            META_FILE.unlink()
        except Exception:
            pass
    return count


def get_cache_stats() -> dict:
    meta = _load_meta()
    total_size = sum(entry.get("size", 0) for entry in meta.values())
    return {
        "entries": len(meta),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "ttl_days": CACHE_TTL / 86400,
        "max_size_mb": MAX_CACHE_SIZE_MB,  # исправлено: не делим на 1024 дважды
    }