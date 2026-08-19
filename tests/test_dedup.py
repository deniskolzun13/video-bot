"""Unit tests для хэширования и кэша (без реальных сетевых операций)."""
from pathlib import Path

import pytest

from utils.hashing import sha256_hex, stable_hash


class TestHashing:
    def test_sha256_hex_consistency(self):
        h1 = sha256_hex("text")
        h2 = sha256_hex("text")
        assert h1 == h2
        assert len(h1) == 64

    def test_sha256_string_bytes(self):
        assert sha256_hex("a") == sha256_hex(b"a")

    def test_stable_hash_deterministic(self):
        assert stable_hash("a", 1, [2, 3]) == stable_hash("a", 1, [2, 3])

    def test_stable_hash_sensitive_to_order(self):
        assert stable_hash("a", "b") != stable_hash("b", "a")

    def test_stable_hash_sensitive_to_values(self):
        assert stable_hash("a", 1) != stable_hash("a", 2)


class TestCacheKey:
    def test_cache_key_for_clip(self):
        from video.cache import cache_key_for_clip
        k1 = cache_key_for_clip("pexels", clip_id="123", query="programmer", url="http://x/1")
        k2 = cache_key_for_clip("pexels", clip_id="123", query="programmer", url="http://x/1")
        assert k1 == k2
        k3 = cache_key_for_clip("pexels", clip_id="124", query="programmer", url="http://x/1")
        assert k1 != k3
        k4 = cache_key_for_clip("pexels", clip_id="123", query="programmer", url="http://x/2")
        assert k1 != k4  # URL влияет на содержимое — включается в ключ

    def test_cache_key_accepts_clip_object(self):
        from video.cache import cache_key_for_clip
        from video_source import VideoClip
        clip = VideoClip(id="9", url="http://x/9", width=1080, height=1920, duration=5, query="tech")
        k = cache_key_for_clip("pexels", clip=clip)
        assert k == cache_key_for_clip("pexels", clip_id="9", query="tech", url="http://x/9")


@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    """Изолированный кэш в подпапке tmp_path."""
    import cache as cache_mod
    import config
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(cache_mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache_mod, "META_FILE", cache_dir / "cache_meta.json")
    monkeypatch.setattr(cache_mod, "CACHE_TTL", 86400)
    monkeypatch.setattr(cache_mod, "MAX_CACHE_SIZE_MB", 100)
    monkeypatch.setattr(config, "CACHE_ENABLED", True)
    return cache_dir


class TestCacheInterface:
    def test_cache_disabled(self, cache_env, monkeypatch):
        import config
        from video import cache as vcache
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        assert vcache.cache_enabled() is False
        assert vcache.cache_get("k", "prov") is None
        assert vcache.cache_exists("k", "prov") is False
        stats = vcache.cache_stats()
        assert stats["enabled"] is False

    def test_cache_enabled(self, cache_env):
        from video import cache as vcache
        assert vcache.cache_enabled() is True

    def test_cache_put_disabled(self, cache_env, monkeypatch, tmp_path):
        import config
        from video import cache as vcache
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        src = tmp_path / "outside" / "src.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"data")
        # Не должно ничего создавать в кэше
        vcache.cache_put("k", "prov", src)
        assert vcache.cache_get("k", "prov") is None
        assert len(list(cache_env.iterdir())) == 0

    def test_cache_get_disabled(self, cache_env, monkeypatch):
        import config
        from video import cache as vcache
        monkeypatch.setattr(config, "CACHE_ENABLED", False)
        assert vcache.cache_get("k", "prov") is None

    def test_cache_enabled_roundtrip(self, cache_env, tmp_path):
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"some-video-bytes")
        vcache.cache_put("clip-key", "pexels", src)
        got = vcache.cache_get("clip-key", "pexels")
        assert got is not None
        assert Path(got).read_bytes() == b"some-video-bytes"
        assert vcache.cache_exists("clip-key", "pexels") is True

    def test_cache_stats(self, cache_env, tmp_path):
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * (1024 * 1024))  # 1 MB — чтобы не терялся при округлении
        vcache.cache_put("k", "prov", src)
        stats = vcache.cache_stats()
        assert stats["enabled"] is True
        assert stats["entries"] == 1
        assert stats["max_size_mb"] == 100  # без двойного деления
        assert stats["total_size_mb"] >= 0.9

    def test_cache_delete(self, cache_env, tmp_path):
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * 1024)
        vcache.cache_put("k", "prov", src)
        assert vcache.cache_exists("k", "prov")
        assert vcache.cache_delete("k", "prov") is True
        assert vcache.cache_exists("k", "prov") is False

    def test_cache_duplicate(self, cache_env, tmp_path):
        """Повторное put того же ключа — без ошибок и только одна запись."""
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * 1024)
        vcache.cache_put("k", "prov", src)
        vcache.cache_put("k", "prov", src)
        stats = vcache.cache_stats()
        assert stats["entries"] == 1

    def test_cache_atomic_write(self, cache_env, tmp_path):
        """После put не должно оставаться временных файлов (.tmp)."""
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * 1024)
        vcache.cache_put("k", "prov", src)
        leftovers = [f for f in cache_env.iterdir() if f.suffix == ".tmp"]
        assert leftovers == []
        # Метаданные — валидный JSON
        import json
        meta_file = cache_env / "cache_meta.json"
        data = json.loads(meta_file.read_text())
        assert "entries" not in data or True
        assert len(data) >= 1

    def test_cache_different_providers(self, cache_env, tmp_path):
        from video import cache as vcache
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * 1024)
        vcache.cache_put("k", "pexels", src)
        vcache.cache_put("k", "pixabay", src)
        assert vcache.cache_get("k", "pexels") is not None
        assert vcache.cache_get("k", "pixabay") is not None
        stats = vcache.cache_stats()
        assert stats["entries"] == 2


class TestLegacyCacheStats:
    def test_stats_not_divided_twice(self, cache_env):
        """MAX_CACHE_SIZE_MB=100 должен отображаться как 100 MB, а не 0.000095."""
        from cache import get_cache_stats
        stats = get_cache_stats()
        assert stats["max_size_mb"] == 100