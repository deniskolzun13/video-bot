"""Локальный TTS (AI_MODE=local).

Движки:
  espeak-ng — встроенный (установлен по умолчанию на многих системах).
  piper     — если установлен (более качественный, но требует голос).

Безопасность: НЕ используем облачный TTS (Yandex SpeechKit) в локальном
режиме. Если движок недоступен — понятная ошибка, без fallback на облако.
"""
import logging
import shutil
import subprocess
from pathlib import Path

import config
from tts import TTSError, probe_duration

logger = logging.getLogger(__name__)

# Голос по умолчанию: русский (для espeak-ng — ru). Пишется через +eng+
# для английских слов (аккуратно, не обязателен).
DEFAULT_VOICE = "ru"


class LocalTTSUnavailable(TTSError):
    """Локальный TTS недоступен."""


def _detect_engine(engine: str = "") -> str:
    engine = (engine or config.LOCAL_TTS_ENGINE or "espeak-ng").strip().lower()
    if engine == "espeak-ng":
        if not shutil.which("espeak-ng"):
            raise LocalTTSUnavailable(
                "LOCAL TTS unavailable: espeak-ng not installed.",
                "Установите espeak-ng (sudo dnf install espeak-ng) или LOCAL_TTS_ENGINE=piper.",
            )
        return "espeak-ng"
    if engine == "piper":
        if not shutil.which("piper"):
            raise LocalTTSUnavailable(
                "LOCAL TTS unavailable: piper not installed.",
                "Установите piper (pip install piper-tts) и голос.",
            )
        return "piper"
    raise LocalTTSUnavailable(
        f"Unknown LOCAL_TTS_ENGINE: {engine}",
        "Поддерживаются: espeak-ng, piper",
    )


def _split_chunks(text: str, limit: int = 4000) -> list[str]:
    """Разбивка на чанки по предложениям (лимит аргументов espeak-ng)."""
    from subtitles import split_sentences

    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            while sentence:
                chunks.append(sentence[:limit])
                sentence = sentence[limit:]
            continue
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]


def _synthesize_espeak_chunk(chunk: str, dest: Path, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> None:
    """Синтез одного чанка через espeak-ng в WAV."""
    cmd = [
        "espeak-ng",
        "-v", voice,
        "-s", str(max(120, int(160 * speed))),
        "-w", str(dest),
        chunk,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists():
        raise LocalTTSUnavailable(
            "espeak-ng не смог синтезировать речь",
            (result.stderr or "")[:200],
        )


def _synthesize_piper_chunk(chunk: str, dest: Path, speed: float = 1.0) -> None:
    """Синтез через piper (если установлен). Требует модель по умолчанию."""
    cmd = ["piper", "--output_file", str(dest), "--length_scale", str(1.1 / max(speed, 0.5))]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _, stderr = proc.communicate(chunk, timeout=120)
    if proc.returncode != 0 or not dest.exists():
        raise LocalTTSUnavailable(
            "piper не смог синтезировать речь",
            (stderr or "")[:200],
        )


def synthesize_local(
    text: str,
    work_dir: Path,
    voice: str = "",
    speed: float | None = None,
    engine: str = "",
) -> tuple[Path, float]:
    """Локальный синтез. Возвращает (путь к mp3, длительность в секундах).

    voice — предпочтительный голос (для espeak-ng — язык, например ru).
    НЕ использует облачный TTS. При недоступности движка — LocalTTSUnavailable.
    """
    text = (text or "").strip()
    if not text:
        raise TTSError("Пустой текст для озвучки")
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    speed = float(speed if speed is not None else config.TTS_SPEED)
    engine = _detect_engine(engine)
    chunks = _split_chunks(text)

    wav_paths: list[Path] = []
    for i, chunk in enumerate(chunks):
        wav = work_dir / f"tts_{i:03d}.wav"
        if engine == "espeak-ng":
            _synthesize_espeak_chunk(chunk, wav, voice=voice or DEFAULT_VOICE, speed=speed)
        else:
            _synthesize_piper_chunk(chunk, wav, speed=speed)
        wav_paths.append(wav)

    # Склейка чанков в один WAV (внахлёст/последовательно, без облака)
    audio_path = work_dir / "tts_audio.mp3"
    if len(wav_paths) == 1:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(wav_paths[0]),
               "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
    else:
        inputs: list[str] = []
        for w in wav_paths:
            inputs += ["-i", str(w)]
        concat = ";".join(
            f"[{i}:a][{i + 1}:a]concat=n=2:v=0:a=1[a{i}]" if i == 0 else f"[a{i - 1}][{i + 1}:a]concat=n=2:v=0:a=1[a{i}]"
            for i in range(len(wav_paths) - 1)
        )
        last_label = f"[a{len(wav_paths) - 2}]"
        cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
               "-filter_complex", concat, "-map", last_label,
               "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise LocalTTSUnavailable(
            "Ошибка ffmpeg при сборке аудио",
            (result.stderr or "")[-300:],
        )

    duration = probe_duration(audio_path)
    logger.info("Local TTS (%s) готов: %s, %.2f с", engine, audio_path.name, duration)
    return audio_path, duration


__all__ = ["synthesize_local", "LocalTTSUnavailable", "DEFAULT_VOICE"]