import json
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
    cancel_token=None,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Склейка клипов (вертикаль/квадрат/горизонталь, центр-кроп), озвучка,
    hardsub-субтитры через ffmpeg.
    clips: [(путь, длительность_сегмента, сдвиг_внутри_видео)].
    cancel_token — опциональный CancellationToken: при отмене ffmpeg
    завершается через terminate()/kill() и поднимается CancellationError.
    width/height — целевое разрешение (по умолчанию из config)."""
    from utils.cancellation import CancellationError

    if cancel_token:
        cancel_token.check()
    out_path = Path(out_path)
    width = width or config.VIDEO_WIDTH
    height = height or config.VIDEO_HEIGHT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(duration for _, duration, _ in clips)

    cmd = ["ffmpeg", "-y"]
    for clip_path, _, _ in clips:
        cmd += ["-stream_loop", "-1", "-i", str(clip_path)]
    cmd += ["-i", str(audio_path)]

    # Фоновая музыка
    music_input_idx = len(clips) + 1  # индекс входа для музыки
    if config.BACKGROUND_MUSIC:
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
                f"[b{i}]scale={{width}}:{{height}}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={{width}}:{{height}},"
                f"boxblur=20:5[bg{i}];"
                f"[f{i}]scale={{width}}:{{height}}:"
                f"force_original_aspect_ratio=decrease[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,"
                f"setsar=1,"
                f"{trim},setpts=PTS-STARTPTS[v{i}]"
            )
        else:
            chain = (
                f"[{i}:v]scale={{width}}:{{height}}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={{width}}:{{height}},"
                f"setsar=1,"
                f"{trim},setpts=PTS-STARTPTS[v{i}]"
            )
        filters.append(chain)
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    filters.append(f"{concat_in}concat=n={len(clips)}:v=1:a=0[vc]")
    filters.append(f"[vc]ass={_escape_filter_path(ass_path)}[vout]")

    audio_index = len(clips)  # индекс основной аудио (после всех видеоклипов)

    if config.BACKGROUND_MUSIC:
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
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = proc.communicate(timeout=config.RENDER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise ValueError(
            f"Рендер занял больше {config.RENDER_TIMEOUT_SECONDS:.0f} с — прервано"
        )
    if cancel_token and cancel_token.is_cancelled:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise CancellationError(cancel_token.reason)
    if proc.returncode != 0:
        raise ValueError(f"Ошибка ffmpeg при рендере: {(stderr or '')[-1000:]}")
    logger.info("Готово: %s", out_path)
    return out_path


def probe_video(path: Path) -> dict:
    """Возвращает информацию о видео через ffprobe (JSON). Пусто при ошибке."""
    path = Path(path)
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def validate_output(path: Path, target_w: int | None = None, target_h: int | None = None) -> dict:
    """Проверяет готовый MP4: существует, размер > 0, есть video+audio stream,
    разрешение совпадает с целевым, длительность > 0, читается ffmpeg.
    Возвращает {ok: bool, reasons: [...], duration, resolution}.
    """
    target_w = target_w or config.VIDEO_WIDTH
    target_h = target_h or config.VIDEO_HEIGHT
    path = Path(path)
    reasons: list[str] = []

    if not path.exists():
        return {"ok": False, "reasons": ["файл не существует"], "duration": 0, "resolution": None}
    if path.stat().st_size <= 0:
        return {"ok": False, "reasons": ["файл пустой"], "duration": 0, "resolution": None}

    info = probe_video(path)
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        reasons.append("нет видео-потока")
    if not audio_streams:
        reasons.append("нет аудио-потока")

    try:
        duration = float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        reasons.append("длительность <= 0")

    resolution = None
    if video_streams:
        vs = video_streams[0]
        fw = int(vs.get("width") or 0)
        fh = int(vs.get("height") or 0)
        resolution = (fw, fh)
        if fw != target_w or fh != target_h:
            reasons.append(f"разрешение {fw}x{fh} вместо {target_w}x{target_h}")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "duration": duration,
        "resolution": resolution,
    }