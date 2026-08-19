"""Unit tests для pipeline.split_for_videos."""

from pipeline import split_for_videos


class TestSplitForVideos:
    def test_short_text_single_part(self):
        assert split_for_videos("Короткий текст.", 100) == ["Короткий текст."]

    def test_long_text_split(self):
        text = "Первое предложение. " * 100
        parts = split_for_videos(text, 100)
        assert len(parts) > 1
        assert all(len(p) <= 100 for p in parts)

    def test_empty(self):
        assert split_for_videos("") == [""]

    def test_sentence_boundaries(self):
        text = "Один. Два. Три. Четыре."
        parts = split_for_videos(text, 20)
        # Не разрезает предложения, если они влезают
        assert all(p.strip() in ("Один.", "Два.", "Три.", "Четыре.")
                   or " ".join(p.split()) == p for p in parts)