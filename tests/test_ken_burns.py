"""Unit tests для Ken Burns fallback (IMAGE_FALLBACK / IMAGE_KEN_BURNS)."""
from pathlib import Path


import config
from script.scene_planner import Scene
from video.fallback import make_photo_clip, make_fallback_clip
from video.selector import VideoSelector
from video_source import VideoPhoto


class FakePhotoProvider:
    """Провайдер только с фото-поиском (без видео)."""

    def __init__(self, photos: list[VideoPhoto]):
        self.photos = photos

    async def search(self, query: str, per_page: int = 5):
        return []

    async def search_photos(self, query: str, per_page: int = 5):
        return self.photos


class FakeVideoProvider:
    """Провайдер с пустым видео и одним вертикальным фото."""

    def __init__(self):
        self.photo = VideoPhoto(
            id="p1", url="http://example.test/photo.jpg",
            width=1080, height=1920, query="test",
        )

    async def search(self, query: str, per_page: int = 5):
        return []

    async def search_photos(self, query: str, per_page: int = 5):
        return [self.photo]


class TestPhotoPicker:
    async def test_picks_vertical_photo_first(self, tmp_path):
        config.PIXABAY_API_KEY = ""
        provider = FakePhotoProvider([
            VideoPhoto(id="wide", url="u1", width=1920, height=1080, query="q"),
            VideoPhoto(id="vert", url="u2", width=1080, height=1920, query="q"),
        ])
        selector = VideoSelector(provider, tmp_path / "work")
        scene = Scene(visual="gaming setup", keywords=["gaming"], duration_hint=4.0, phrase_indexes=[0])
        photo = await selector._pick_photo(scene)
        assert photo is not None
        assert photo.id == "vert"

    async def test_no_photo_returns_none(self, tmp_path):
        config.PIXABAY_API_KEY = ""
        selector = VideoSelector(FakePhotoProvider([]), tmp_path / "work")
        scene = Scene(visual="gaming", keywords=[], duration_hint=4.0, phrase_indexes=[0])
        assert await selector._pick_photo(scene) is None

    async def test_photo_fallback_uses_make_photo_clip(self, tmp_path, monkeypatch):
        config.PIXABAY_API_KEY = ""
        config.IMAGE_FALLBACK = True
        calls = {}

        def fake_make_photo_clip(dest, url, duration):
            calls["url"] = url
            calls["dest"] = dest
            Path(dest).write_bytes(b"photo-mp4")

        monkeypatch.setattr("video.selector.make_photo_clip", fake_make_photo_clip)
        selector = VideoSelector(FakeVideoProvider(), tmp_path / "work")
        scene = Scene(visual="gaming setup", keywords=["gaming"], duration_hint=4.0, phrase_indexes=[0])
        dest = tmp_path / "clip_000.mp4"
        made = await selector._photo_fallback(scene, dest, 4.0)
        assert made is True
        assert calls["url"] == "http://example.test/photo.jpg"

    async def test_select_with_image_fallback(self, tmp_path, monkeypatch):
        config.PIXABAY_API_KEY = ""
        config.IMAGE_FALLBACK = True
        calls = []

        def fake_make_photo_clip(dest, url, duration):
            calls.append(url)
            Path(dest).write_bytes(b"photo-mp4")

        monkeypatch.setattr("video.selector.make_photo_clip", fake_make_photo_clip)
        selector = VideoSelector(FakeVideoProvider(), tmp_path / "work")
        scenes = [Scene(visual="gaming setup", keywords=["gaming"], duration_hint=4.0, phrase_indexes=[0])]
        clips = await selector.select(scenes, [(0.0, 4.0)])
        assert len(clips) == 1
        assert clips[0][0].exists()
        assert calls, "должен быть Ken Burns fallback"


class TestMakePhotoClip:
    def test_make_fallback_still_works(self, tmp_path):
        dest = tmp_path / "g.mp4"
        make_fallback_clip(dest, 2.0, 0)
        assert dest.exists()

    def test_photo_clip_download_failure_falls_to_gradient(self, tmp_path, monkeypatch):
        """Если фото недоступно — Ken Burns падает на градиент."""
        import video.fallback as fallback_mod

        calls = {"gradient": 0}

        def fake_gradient(dest, duration, index=0):
            calls["gradient"] += 1
            Path(dest).write_bytes(b"gradient-mp4")

        def fail_download(*args, **kwargs):
            raise Exception("network down")

        monkeypatch.setattr("httpx.Client", fail_download)
        monkeypatch.setattr(fallback_mod, "make_fallback_clip", fake_gradient)
        dest = tmp_path / "kb.mp4"
        result = make_photo_clip(dest, "http://example.test/x.jpg", 2.0)
        assert result == dest
        assert dest.exists()
        assert calls["gradient"] == 1