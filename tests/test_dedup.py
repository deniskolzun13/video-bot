"""Unit tests для хэширования и кэша (без реальных файловых операций)."""
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
        k1 = cache_key_for_clip("PexelsProvider", "programmer", "123")
        k2 = cache_key_for_clip("PexelsProvider", "programmer", "123")
        assert k1 == k2
        k3 = cache_key_for_clip("PexelsProvider", "programmer", "124")
        assert k1 != k3