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
ASR_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"


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


async def get_word_timestamps(audio_path: Path, text: str) -> list[dict] | None:
    """Получение word-level timestamps через Yandex SpeechKit Speech Recognition API.
    Используется для точного тайминга субтитров.
    Возвращает список {'word': str, 'start': float, 'end': float} или None при ошибке."""
    if not config.YANDEX_FOLDER_ID:
        logger.warning("YANDEX_FOLDER_ID не задан, нельзя получить word-level timestamps")
        return None

    # Конвертируем mp3 в WAV (16kHz mono PCM) для Yandex SpeechKit ASR через ffmpeg
    wav_path = audio_path.with_suffix(".wav")
    cmd = [
        "ffmpeg", "-y", "-v", "quiet",
        "-i", str(audio_path),
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
    if result.returncode != 0:
        logger.warning("Не удалось конвертировать аудио в WAV для ASR: %s", result.stderr.decode()[:200])
        return None

    try:
        audio_data = wav_path.read_bytes()
    except Exception as exc:
        logger.warning("Не удалось прочитать WAV-файл: %s", exc)
        return None
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

    params = {
        "folderId": config.YANDEX_FOLDER_ID,
        "lang": config.TTS_LANG.replace("-", "_").lower(),
    }
    if text:
        params["text"] = text  # использовать как эталон для лучшего распознавания

    headers = _auth_headers()
    headers["Content-Type"] = "audio/wav"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                ASR_URL,
                params=params,
                headers=headers,
                content=audio_data,
            )
        if response.status_code != 200:
            logger.warning("Yandex ASR вернул %d, word-level timestamps недоступны", response.status_code)
            return None

        result = response.json()
        words: list[dict] = []
        for hyp in result.get("hypotheses", []):
            if hyp.get("confidence", 0) < 0.5:
                continue
            for w in hyp.get("words", []):
                words.append({
                    "word": w.get("word", "").strip(),
                    "start": w.get("start", 0.0),
                    "end": w.get("end", 0.0),
                })
            if words:
                break

        if not words:
            logger.warning("Yandex ASR не вернул слова для word-level timestamps")
            return None

        logger.info("Получены word-level timestamps: %d слов", len(words))
        return words
    except Exception as exc:
        logger.warning("Ошибка получения word-level timestamps: %s", exc)
        return None


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
        try:
            err_detail = response.json().get("error", {}).get("message", "")
        except Exception:
            err_detail = ""
        status_messages = {
            401: "Неверный API-ключ Yandex SpeechKit. Проверьте TELEGRAM_BOT_TOKEN и YANDEX_API_KEY.",
            403: "Доступ к SpeechKit запрещён. Проверьте тариф и доступность сервиса.",
            429: "Превышен лимит запросов к SpeechKit. Подождите несколько минут.",
            503: "Сервис SpeechKit временно недоступен. Попробуйте позже.",
        }
        user_msg = status_messages.get(response.status_code, "Ошибка синтеза речи. Попробуйте позже.")
        if err_detail:
            user_msg += f" ({err_detail})"
        raise APIError(
            "Yandex SpeechKit",
            user_msg,
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
                logger.error("TTS API error: %s (status=%d)", exc, exc.status_code)
                raise TTSError(str(exc), exc.details or "") from exc
            pcm_paths.append(pcm)

    audio_path = work_dir / "tts_audio.mp3"
    if len(pcm_paths) == 1:
        cmd = ["ffmpeg", "-y", "-f", "s16le", "-ar", str(config.TTS_SAMPLE_RATE), "-ac", "1", "-i", str(pcm_paths[0]),
               "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
    else:
        # Кроссфейд между чанками для плавного перехода интонации
        crossfade = config.TTS_CROSSFADE if not config.TTS_DISABLE_CROSSFADE else 0
        if crossfade <= 0:
            # Склейка внахлёст без кроссфейда
            filter_chain = []
            for i in range(len(pcm_paths) - 1):
                filter_chain.append(f"[{i}:a][{i+1}:a]concat=v=0:a=1[at{i}]")
            concat_label = f"[at{len(pcm_paths)-2}]"
            filter_str = ";".join(filter_chain)
            cmd = ["ffmpeg", "-y"]
            for pcm in pcm_paths:
                cmd += ["-f", "s16le", "-ar", str(config.TTS_SAMPLE_RATE), "-ac", "1", "-i", str(pcm)]
            cmd += ["-filter_complex", filter_str, "-map", concat_label,
                    "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
        else:
            cf = []
            for i in range(len(pcm_paths) - 1):
                if i == 0:
                    cf.append(f"[{i}:a][{i+1}:a]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri[a{i}{i+1}]")
                else:
                    cf.append(f"[a{i-1}{i}][{i+1}:a]acrossfade=d={crossfade:.3f}:c1=tri:c2=tri[a{i}{i+1}]")
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