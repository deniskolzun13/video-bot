"""Хэширование для кэша (SHA-256)."""
import hashlib
import json


def sha256_hex(data: bytes | str) -> str:
    """SHA-256 в hex-строке."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def stable_hash(*parts) -> str:
    """Стабильный хэш от набора строк/чисел/структур.
    Порядок аргументов важен — меняется результат."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()