import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

import httpx

import config
from subtitles import split_sentences

logger = logging.getLogger(__name__)

TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"


def _auth_headers() -> dict[str, str]:
    if config.YANDEX_API_KEY:
        return {"Authorization": f"Api-Key {config.YANDEX_API_KEY}"}
    if config.YANDEX_IAM_TOKEN:
        return {"Authorization": f"Bearer {config.YANDEX_IAM_TOKEN}"}
    raise ValueError("Задай YANDEX_API_KEY или YANDEX_IAM_TOKEN в .env")


def split_into_chunks(text: str, limit: int = config.TTS_MAX_CHUNK) -> list[str]:
    """Разбивка на чанки по границам предложений (лимит Yandex SpeechKit ~5000 символов)."""
    text = re.sub(r"\s+", " ", text).strip()
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        while len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


async def _synthesize_chunk(client: httpx.AsyncClient, chunk: str, dest: Path) -> None:
    params = {
        "text": chunk,
        "lang": config.TTS_LANG,
        "voice": config.TTS_VOICE,
        "emotion": config.TTS_EMOTION,
        "speed": str(config.TTS_SPEED),
        "format": "lpcm",
        "sampleRateHertz": str(config.TTS_SAMPLE_RATE),
    }
    if config.YANDEX_FOLDER_ID:
        params["folderId"] = config.YANDEX_FOLDER_ID
    response = await client.post(TTS_URL, params=params, headers=_auth_headers())
    if response.status_code != 200:
        raise ValueError(
            f"Yandex SpeechKit вернул ошибку {response.status_code}: {response.text[:300]}"
        )
    dest.write_bytes(response.content)


async def synthesize(text: str, work_dir: Path) -> tuple[Path, float]:
    """Синтез речи. Возвращает (путь к mp3, длительность в секундах)."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    chunks = split_into_chunks(text)
    pcm_paths: list[Path] = []
    async with httpx.AsyncClient(timeout=180) as client:
        for i, chunk in enumerate(chunks):
            logger.info("TTS: чанк %d/%d (%d символов)", i + 1, len(chunks), len(chunk))
            pcm = work_dir / f"tts_{i:03d}.pcm"
            await _synthesize_chunk(client, chunk, pcm)
            pcm_paths.append(pcm)

    audio_path = work_dir / "tts_audio.mp3"
    cmd = ["ffmpeg", "-y"]
    for pcm in pcm_paths:
        cmd += ["-f", "s16le", "-ar", str(config.TTS_SAMPLE_RATE), "-ac", "1", "-i", str(pcm)]
    if len(pcm_paths) > 1:
        chain = "".join(f"[{i}:a]" for i in range(len(pcm_paths)))
        cmd += ["-filter_complex", f"{chain}concat=n={len(pcm_paths)}:v=0:a=1[a]"]
        cmd += ["-map", "[a]"]
    cmd += ["-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
    await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)

    duration = await asyncio.to_thread(probe_duration, audio_path)
    logger.info("TTS готов: %s, длительность %.2f с", audio_path.name, duration)
    return audio_path, duration


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True,
        check=True,
        text=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])