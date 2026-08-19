"""Unit tests для видео-ранжирования и защиты от дубликатов."""
from video.ranking import score_clip
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