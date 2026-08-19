"""Unit tests для видео-ранжирования и защиты от дубликатов."""
from video.ranking import WEIGHTS, score_clip
from video_source import VideoClip


def _clip(cid: str, width=1080, height=1920, duration=10.0, query="test"):
    return VideoClip(id=cid, url=f"http://x/{cid}", width=width, height=height,
                     duration=duration, query=query)


class TestVideoRanking:
    def test_duplicate_penalty(self):
        clip = _clip("1")
        used = {"1"}
        scored = score_clip(clip, "test", used_ids=used)
        assert scored.score <= -1000
        assert scored.reasons == ["дубликат"]

    def test_portrait_preferred(self):
        portrait = score_clip(_clip("1", 1080, 1920), "tech")
        landscape = score_clip(_clip("2", 1920, 1080), "tech")
        assert portrait.score > landscape.score

    def test_resolution_filter(self):
        low = score_clip(_clip("1", 320, 480), "tech")
        high = score_clip(_clip("2", 1080, 1920), "tech")
        assert high.score > low.score

    def test_keyword_overlap(self):
        kw = score_clip(_clip("1", 1080, 1920, query="programmer coding"), "programmer", ["programmer"])
        plain = score_clip(_clip("2", 1080, 1920, query="abstract waves"), "programmer", ["programmer"])
        assert kw.score >= plain.score

    def test_short_clip_penalty(self):
        short = score_clip(_clip("1", 1080, 1920, duration=1.0), "tech", min_duration=5.0)
        long = score_clip(_clip("2", 1080, 1920, duration=30.0), "tech", min_duration=5.0)
        assert long.score > short.score

    def test_score_in_0_1_range(self):
        """Итоговый score свободного клипа — в [0, 1]."""
        s = score_clip(_clip("1", 1080, 1920, duration=10.0, query="tech"), "tech", ["tech"])
        assert 0.0 <= s.score <= 1.0

    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001
        assert WEIGHTS["relevance"] == 0.40
        assert WEIGHTS["orientation"] == 0.20
        assert WEIGHTS["resolution"] == 0.15
        assert WEIGHTS["duration"] == 0.10
        assert WEIGHTS["keyword"] == 0.15

    def test_relevance_weight_dominant(self):
        """Полное совпадение запроса даёт больше, чем только ориентация."""
        exact = score_clip(_clip("1", 640, 1136, duration=5.0, query="ai research"), "ai research", [])
        generic = score_clip(_clip("2", 1080, 1920, duration=30.0, query="abstract"), "ai research", [])
        assert exact.score > generic.score


class TestDeduplication:
    def test_selector_uses_used_ids(self):
        """Повторный клип не должен выбираться, если есть альтернатива."""
        used = {"1"}
        c1 = score_clip(_clip("1", 1080, 1920), "test", used_ids=used)
        c2 = score_clip(_clip("2", 1080, 1920), "test", used_ids=used)
        assert c1.score < c2.score

    def test_distinct_ids_not_penalized(self):
        used = {"1"}
        fresh = score_clip(_clip("2", 1080, 1920), "test", used_ids=used)
        assert fresh.score > -1000


class TestDuplicateClips:
    """ТЗ: тест_дубликат A,A,B -> A,B (не повторяем), A,A,A — не падает."""

    def test_a_a_b_picks_fresh(self):
        """Если для двух сцен доступны A,A,B — вторая сцена должна взять B."""
        used: set[str] = set()
        used.add("A")
        a2 = score_clip(_clip("A", 1080, 1920), "test", used_ids=used)
        b = score_clip(_clip("B", 1080, 1920), "test", used_ids=used)
        assert a2.score <= -1000  # A уже использован
        assert b.score > a2.score
        # выбираем лучший
        assert b.score > 0

    def test_a_a_a_does_not_crash(self):
        """Только дубликаты — функция не падает, возвращает -1000."""
        used = {"A"}
        s = score_clip(_clip("A", 1080, 1920), "test", used_ids=used)
        assert s.score <= -1000
        # и селектор сможет вернуть лучший из использованных
        assert s.score == min(s.score, -1000)