"""Fallback: если видео не найдено — используем сгенерированный фон.

Порядок (см. ТЗ v2.0.1):
  1. Если IMAGE_FALLBACK=true — стоковое фото (Pexels/Pixabay) с эффектом
     Ken Burns (медленный zoom/pan через zoompan).
  2. Иначе — анимированный градиент (ffmpeg lavfi).

Ролик не падает только потому, что Pexels/Steam не нашли видео.
"""
import logging
import subprocess
from pathlib import Path

import httpx

import config
from video_render import _detect_h264_encoder

logger = logging.getLogger(__name__)

FALLBACK_COLORS = [
    ("0x1a1a2e", "0x16213e"),
    ("0x0f3460", "0x533483"),
    ("0x16213e", "0x0f3460"),
    ("0x1a1a2e", "0x0f3460"),
]


def make_fallback_clip(dest: Path, duration: float, index: int = 0) -> Path:
    """Создаёт градиентный видео-фон длительностью duration через ffmpeg."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    c1, c2 = FALLBACK_COLORS[index % len(FALLBACK_COLORS)]
    try:
        encoder = _detect_h264_encoder()
    except ValueError as exc:
        logger.warning("Нет H.264-энкодера, fallback-фон пропущен: %s", exc)
        return dest
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", (
            f"gradients=size={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:"
            f"c0={c1}:c1={c2}:d={max(duration, 2.0):.2f}:speed=0.01"
        ),
        "-c:v", encoder, "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(config.FPS),
        "-t", f"{max(duration, 2.0):.2f}",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("Не удалось сгенерировать fallback-фон: %s", result.stderr[-300:])
        # Последняя надежда: простой чёрный цвет
        cmd[5] = f"color=black:size={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:rate={config.FPS}"
        subprocess.run(cmd, capture_output=True, text=True)
    logger.info("Fallback-фон создан: %s (%.1fс)", dest.name, duration)
    return dest


def make_photo_clip(dest: Path, photo_url: str, duration: float) -> Path:
    """Скачивает фото и создаёт видеоклип с эффектом Ken Burns.

    zoompan медленно приближает кадр (Ken Burns), результирующее видео
    длительностью duration приведено к целевому разрешению 9:16.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image = dest.with_suffix(".jpg")

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.get(photo_url)
            response.raise_for_status()
            image.write_bytes(response.content)
    except Exception as exc:
        logger.warning("Не удалось скачать фото %s: %s", photo_url, exc)
        make_fallback_clip(dest, duration, 0)
        return dest

    seconds = max(duration, 2.0)
    w, h = config.VIDEO_WIDTH, config.VIDEO_HEIGHT
    fps = max(int(config.FPS), 1)
    total_frames = int(seconds * fps)
    # Период одного «кадра» Ken Burns (сек). IMAGE_KEN_BURNS=4 -> зум повторяется
    # каждые 4 секунды, как смена статичного кадра с медленным приближением.
    ken_seconds = max(float(getattr(config, "IMAGE_KEN_BURNS", 4.0)), 1.0)
    period = max(int(ken_seconds * fps), 1)
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"zoompan=z='1+0.12*mod(on,{period})/{period}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={w}x{h}:fps={fps},"
        f"format=yuv420p"
    )
    try:
        encoder = _detect_h264_encoder()
    except ValueError as exc:
        logger.warning("Нет H.264-энкодера, Ken Burns пропущен: %s", exc)
        image.unlink(missing_ok=True)
        return dest
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(image),
        "-vf", vf,
        "-c:v", encoder, "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-t", f"{seconds:.2f}",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    image.unlink(missing_ok=True)
    if result.returncode != 0:
        logger.warning("Ken Burns не удался (%s), падаю на градиент", result.stderr[-300:])
        make_fallback_clip(dest, duration, 0)
        return dest
    logger.info("Ken Burns клип создан: %s (%.1fс)", dest.name, duration)
    return dest