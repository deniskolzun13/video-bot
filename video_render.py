import logging
import re
import subprocess
from pathlib import Path

import config

logger = logging.getLogger(__name__)

H264_ENCODERS = ("libx264", "libopenh264", "h264_vaapi", "h264_v4l2m2m")


def _detect_h264_encoder() -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
    )
    for name in H264_ENCODERS:
        if re.search(rf"\b{re.escape(name)}\b", result.stdout):
            return name
    raise ValueError(
        "ffmpeg без H.264-энкодера. Установи пакет с x264/openh264 "
        "(например, sudo apt install ffmpeg)"
    )


def _escape_filter_path(path: Path) -> str:
    value = str(path).replace("\\", "/")
    value = value.replace("'", "\\'")
    value = value.replace(":", "\\:")
    return value


def render_video(
    clips: list[tuple[Path, float, float]],
    audio_path: Path,
    ass_path: Path,
    out_path: Path,
) -> Path:
    """Склейка клипов (9:16, центр-кроп), озвучка, hardsub-субтитры через ffmpeg.
    clips: [(путь, длительность_сегмента, сдвиг_внутри_видео)]."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(duration for _, duration, _ in clips)

    cmd = ["ffmpeg", "-y"]
    for clip_path, _, _ in clips:
        cmd += ["-stream_loop", "-1", "-i", str(clip_path)]
    cmd += ["-i", str(audio_path)]

    # Фоновая музыка
    music_input_idx = len(clips) + 1  # индекс входа для музыки
    if config.BACKGROUND_MUSIC:
        from pathlib import Path
        music_path = Path(config.BG_MUSIC_PATH)
        if music_path.exists():
            cmd += ["-stream_loop", "-1", "-i", str(music_path)]
        else:
            logger.warning("Фоновая музыка включена, но файл не найден: %s", config.BG_MUSIC_PATH)

    encoder = _detect_h264_encoder()
    extra = ["-pix_fmt", "yuv420p"]
    if encoder == "libopenh264":
        extra = ["-pix_fmt", "yuv420p", "-allow_skip_frames", "0", "-maxrate", "4000k"]

    filters: list[str] = []
    for i, (_, duration, start) in enumerate(clips):
        trim = f"trim=start={start:.3f}:duration={duration:.3f}" if start else f"trim=duration={duration:.3f}"
        if config.VIDEO_PADDING == "blur":
            chain = (
                f"[{i}:v]split[b{i}][f{i}];"
                f"[b{i}]scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT},"
                f"boxblur=20:5[bg{i}];"
                f"[f{i}]scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:"
                f"force_original_aspect_ratio=decrease[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,"
                f"setsar=1,"
                f"{trim},setpts=PTS-STARTPTS[v{i}]"
            )
        else:
            chain = (
                f"[{i}:v]scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT},"
                f"setsar=1,"
                f"{trim},setpts=PTS-STARTPTS[v{i}]"
            )
        filters.append(chain)
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    filters.append(f"{concat_in}concat=n={len(clips)}:v=1:a=0[vc]")
    filters.append(f"[vc]ass={_escape_filter_path(ass_path)}[vout]")

    audio_index = len(clips)  # индекс основной аудио (после всех видеоклипов)

    if config.BACKGROUND_MUSIC:
        from pathlib import Path
        music_path = Path(config.BG_MUSIC_PATH)
        if music_path.exists():
            filters.append(
                f"[{audio_index}:a]asetrate=48000[aout];"
                f"[{music_input_idx}:a]aloop=loop=-1:size=2e10[bg];"
                f"[bg]volume={config.BG_MUSIC_VOLUME}dB[bgm];"
                f"[aout][bgm]sidechaincompress=threshold=0.0004:ratio=4:attack=10:release=100[mix]"
            )
            audio_output = "[mix]"
        else:
            audio_output = f"{audio_index}:a"
    else:
        audio_output = f"{audio_index}:a"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", audio_output,
        "-c:v", encoder,
        "-preset", "medium",
        "-crf", "20",
        "-r", str(config.FPS),
        *extra,
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{total:.2f}",
        "-movflags", "+faststart",
        str(out_path),
    ]
    logger.info("ffmpeg: рендер %s (%.1f с, %d клипов)", out_path.name, total, len(clips))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"Ошибка ffmpeg при рендере: {result.stderr[-1000:]}")
    logger.info("Готово: %s", out_path)
    return out_path