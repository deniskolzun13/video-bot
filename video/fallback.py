"""Fallback: если видео не найдено — используем сгенерированный фон.

Минимальный вариант без внешних зависимостей: ffmpeg генерирует градиентный фон
(движущийся) длительностью нужной фразы. Ролик не падает только потому, что
Pexels/Steam не нашли видео.
"""
import logging
import subprocess
from pathlib import Path

import config

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
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", (
            f"gradients=size={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:"
            f"c0={c1}:c1={c2}:d={max(duration, 2.0):.2f}:speed=0.01"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
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