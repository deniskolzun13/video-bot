"""Unit tests для config.py"""
import config


def test_required_config():
    assert config.VIDEO_WIDTH == 1080
    assert config.VIDEO_HEIGHT == 1920
    assert config.FPS == 30
    assert config.MAX_PARTS >= 1
    assert config.VIDEO_SOURCE in ("auto", "steam", "pexels")


def test_new_v2_config():
    assert config.LLM_PROVIDER.lower() == "openai"
    assert config.SUBTITLE_STYLE in ("classic", "tiktok", "news", "gaming", "minimal")
    assert config.CACHE_ENABLED in (True, False)
    assert config.SCRIPT_GENERATION in ("on", "off", "auto")
    assert config.SCENES_MAX >= 3
    assert config.DATA_DIR


def test_tts_defaults():
    assert config.TTS_LANG == "ru-RU"
    assert 0 < config.TTS_CROSSFADE < 1


def test_video_source_defaults():
    assert config.MIN_CLIP_WIDTH >= 0
    assert config.MIN_CLIPS_PER_PHRASE >= 1


def test_subtitle_defaults():
    assert config.SUB_FONTSIZE > 0
    assert config.SUB_MARGIN_V >= 0
    assert config.SUB_OUTLINE_WIDTH > 0


class TestFormat:
    def test_vertical_default(self):
        assert config.resolve_video_size(None) == (1080, 1920)
        assert config.resolve_video_size("vertical") == (1080, 1920)

    def test_square(self):
        assert config.resolve_video_size("square") == (1080, 1080)

    def test_landscape(self):
        assert config.resolve_video_size("landscape") == (1920, 1080)

    def test_unknown_falls_back_to_vertical(self):
        assert config.resolve_video_size("bogus") == (1080, 1920)