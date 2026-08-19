"""Настоящий FFmpeg smoke test: полный рендер + валидация.

Требует установленного ffmpeg (в CI он ставится отдельным шагом).
Пропускается (skip), если ffmpeg без H.264-энкодера.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from video_render import render_video, validate_output


def _h264_encoder() -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
    )
    for name in ("libx264", "libopenh264", "h264_vaapi", "h264_v4l2m2m"):
        if name in result.stdout:
            return name
    raise RuntimeError("no h264 encoder")


def _have_ffmpeg_h264() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        _h264_encoder()
        return True
    except RuntimeError:
        return False


def _make_test_clip(path: Path, seconds: float, color: str = "red") -> Path:
    """Тестовый клип 320x180 (16:9), H.264, 24 fps — эмулирует сток."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi",
         "-i", f"color=c={color}:size=320x180:rate=24:d={seconds}",
         "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-c:v", _h264_encoder(), "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-t", str(seconds),
         str(path)],
        check=True, capture_output=True, text=True,
    )
    return path


def _make_audio(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # sine выдаёт float; mp3-муксер требует int16 — пишем в WAV (pcm_s16le)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(path)],
        check=True, capture_output=True, text=True,
    )
    return path


def _make_ass(path: Path) -> Path:
    """Минимальный ASS с одной строкой субтитра."""
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,64,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        "1,5,2,2,40,40,100,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Тест субтитра\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.skipif(not _have_ffmpeg_h264(), reason="ffmpeg без H.264-энкодера")
class TestFFmpegSmoke:
    def test_full_render_vertical(self, tmp_path):
        """Реальный рендер: клипы + аудио + ASS -> вертикальный mp4 с валидацией."""
        clip1 = _make_test_clip(tmp_path / "a.mp4", 1.5, color="red")
        clip2 = _make_test_clip(tmp_path / "b.mp4", 1.5, color="blue")
        audio = _make_audio(tmp_path / "a.wav", 3.0)
        ass = _make_ass(tmp_path / "s.ass")
        out = tmp_path / "out.mp4"

        render_video(
            [(clip1, 1.5, 0.0), (clip2, 1.5, 0.0)],
            audio, ass, out,
            width=1080, height=1920,
        )

        assert out.exists(), "mp4 не создан"
        assert out.stat().st_size > 0
        check = validate_output(out, target_w=1080, target_h=1920)
        assert check["ok"], f"валидация не пройдена: {check['reasons']}"
        assert check["resolution"] == (1080, 1920)
        assert check["duration"] >= 2.9

    def test_render_square_format(self, tmp_path):
        """Квадратный формат 1:1 — разрешение в валидации соответствует."""
        clip1 = _make_test_clip(tmp_path / "a.mp4", 2.0, color="green")
        audio = _make_audio(tmp_path / "a.wav", 2.0)
        ass = _make_ass(tmp_path / "s.ass")
        out = tmp_path / "out_sq.mp4"

        render_video(
            [(clip1, 2.0, 0.0)],
            audio, ass, out,
            width=1080, height=1080,
        )

        check = validate_output(out, target_w=1080, target_h=1080)
        assert check["ok"], f"валидация не пройдена: {check['reasons']}"
        assert check["resolution"] == (1080, 1080)

    def test_unified_timeline_render(self, tmp_path):
        """Единый рендер: несколько сегментов (news/transition/outro) с аудио
        и субтитрами -> ОДИН mp4 + ffprobe-валидация."""
        from news.models import ITEM_NEWS, ITEM_OUTRO, ITEM_TRANSITION, TimelineItem, UnifiedTimeline
        from video_render_unified import render_unified_timeline

        # сегменты: NEWS 1 (2с) + transition (1с) + NEWS 2 (2с) + outro (1с)
        clips = [
            (str(_make_test_clip(tmp_path / "c1.mp4", 2.0, color="red")), 2.0, 0.0),
            (str(_make_test_clip(tmp_path / "c2.mp4", 1.0, color="blue")), 1.0, 0.0),
            (str(_make_test_clip(tmp_path / "c3.mp4", 2.0, color="green")), 2.0, 0.0),
            (str(_make_test_clip(tmp_path / "c4.mp4", 1.0, color="purple")), 1.0, 0.0),
        ]
        audios = [_make_audio(tmp_path / f"a{i}.wav", 2.0) for i in range(4)]
        audios[1] = _make_audio(tmp_path / "a1.wav", 1.0)
        audios[3] = _make_audio(tmp_path / "a3.wav", 1.0)

        timeline = UnifiedTimeline(items=[
            TimelineItem(id="n1", type=ITEM_NEWS, start=0.0, end=2.0, duration=2.0,
                         news_id=1, text="Первая новость про технологии.",
                         audio_path=str(audios[0]), video_paths=[clips[0]]),
            TimelineItem(id="t1", type=ITEM_TRANSITION, start=2.0, end=3.0, duration=1.0,
                         news_id=1, text="А теперь к следующей.",
                         audio_path=str(audios[1]), video_paths=[clips[1]]),
            TimelineItem(id="n2", type=ITEM_NEWS, start=3.0, end=5.0, duration=2.0,
                         news_id=2, text="Вторая новость про смартфоны.",
                         audio_path=str(audios[2]), video_paths=[clips[2]]),
            TimelineItem(id="o1", type=ITEM_OUTRO, start=5.0, end=6.0, duration=1.0,
                         news_id=None, text="На сегодня всё.",
                         audio_path=str(audios[3]), video_paths=[clips[3]]),
        ])
        ass = tmp_path / "subs.ass"
        out = tmp_path / "unified_out.mp4"

        render_unified_timeline(
            timeline, ass, out,
            news_titles={1: "Новость 1", 2: "Новость 2"},
            width=1080, height=1920,
        )

        assert out.exists(), "mp4 не создан"
        assert out.stat().st_size > 0
        assert ass.exists(), "ASS субтитры не созданы"
        check = validate_output(out, target_w=1080, target_h=1920)
        assert check["ok"], f"валидация не пройдена: {check['reasons']}"
        assert check["resolution"] == (1080, 1920)
        # 6с сегментов минус 3 crossfade по TRANSITION_DURATION (0.5) = ~4.5с
        import config

        assert abs(check["duration"] - (6.0 - 3 * config.TRANSITION_DURATION)) < 0.8