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


class TTSError(Exception):
    """Ошибка синтеза речи — сообщение для пользователя."""
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


class APIError(Exception):
    """Общая ошибка внешнего API."""
    def __init__(self, service: str, message: str, status_code: int = 0):
        super().__init__(message)
        self.service = service
        self.status_code = status_code


def _auth_headers() -> dict[str, str]:
    if config.YANDEX_API_KEY:
        return {"Authorization": f"Api-Key {config.YANDEX_API_KEY}"}
    if config.YANDEX_IAM_TOKEN:
        return {"Authorization": f"Bearer {config.YANDEX_IAM_TOKEN}"}
    raise TTSError("Не настроен Yandex SpeechKit: отсутствует API-ключ или IAM-токен")


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
        raise APIError(
            "Yandex SpeechKit",
            f"Ошибка синтеза речи: {response.status_code}",
            response.status_code,
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
            try:
                await _synthesize_chunk(client, chunk, pcm)
            except APIError as exc:
                raise TTSError("Ошибка при обращении к Yandex SpeechKit. Попробуйте позже.") from exc
            pcm_paths.append(pcm)

    audio_path = work_dir / "tts_audio.mp3"
    if len(pcm_paths) == 1:
        cmd = ["ffmpeg", "-y", "-f", "s16le", "-ar", str(config.TTS_SAMPLE_RATE), "-ac", "1", "-i", str(pcm_paths[0]),
               "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
    else:
        # Кроссфейд между чанками (50 мс) для плавного перехода интонации
        cf = []
        for i in range(len(pcm_paths) - 1):
            if i == 0:
                cf.append(f"[{i}:a][{i+1}:a]acrossfade=d=0.05:c1=tri:c2=tri[a{i}{i+1}]")
            else:
                cf.append(f"[a{i-1}{i}][{i+1}:a]acrossfade=d=0.05:c1=tri:c2=tri[a{i}{i+1}]")
        filter_complex = ";".join(cf)
        last_label = f"[a{len(pcm_paths)-2}{len(pcm_paths)-1}]"
        cmd = ["ffmpeg", "-y"]
        for pcm in pcm_paths:
            cmd += ["-f", "s16le", "-ar", str(config.TTS_SAMPLE_RATE), "-ac", "1", "-i", str(pcm)]
        cmd += ["-filter_complex", filter_complex, "-map", last_label,
                "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
    try:
        await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        logger.exception("Ошибка ffmpeg при склейке аудио")
        raise TTSError("Ошибка при сборке аудиофайла. Попробуйте сократить текст.") from exc

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