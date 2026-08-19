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