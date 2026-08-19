import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

import httpx

import config
from subtitles import split_sentences
from utils.retry import RetryableError, is_retryable_status, retry_async

logger = logging.getLogger(__name__)

TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
ASR_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
ASR_V3_URL = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
ASR_V3_RESULT_URL = "https://stt.api.cloud.yandex.net/stt/v3/getRecognition"


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


async def get_word_timestamps(audio_path: Path, text: str = "") -> list[dict] | None:
    """Получение word-level timestamps через Yandex SpeechKit API v3 (async recognition).
    Используется для точного тайминга субтитров.
    Возвращает список {'word': str, 'start': float, 'end': float} или None при ошибке.
    Параметр text сохранён для совместимости вызовов (API его не принимает)."""
    if not config.YANDEX_FOLDER_ID:
        logger.warning("YANDEX_FOLDER_ID не задан, нельзя получить word-level timestamps")
        return None

    # Конвертируем mp3 в сырой LPCM (16kHz mono s16le) для Yandex SpeechKit ASR v3
    pcm_path = audio_path.with_suffix(".pcm16k")
    cmd = [
        "ffmpeg", "-y", "-v", "quiet",
        "-i", str(audio_path),
        "-ar", "16000", "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "s16le",
        str(pcm_path),
    ]
    result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
    if result.returncode != 0:
        logger.warning("Не удалось конвертировать аудио в PCM для ASR: %s", result.stderr.decode()[:200])
        return None

    try:
        audio_data = pcm_path.read_bytes()
    except Exception as exc:
        logger.warning("Не удалось прочитать PCM-файл: %s", exc)
        return None
    finally:
        try:
            pcm_path.unlink(missing_ok=True)
        except Exception:
            pass

    headers = _auth_headers()
    headers["x-folder-id"] = config.YANDEX_FOLDER_ID

    import base64
    body = {
        "content": base64.b64encode(audio_data).decode(),
        "recognitionModel": {
            "model": "general",
            "audioFormat": {
                "rawAudio": {
                    "audioEncoding": "LINEAR16_PCM",
                    "sampleRateHertz": "16000",
                    "audioChannelCount": "1",
                }
            },
            "languageRestriction": {
                "restrictionType": "WHITELIST",
                "languageCode": [config.TTS_LANG],
            },
        },
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(ASR_V3_URL, json=body, headers=headers)
            if response.status_code != 200:
                logger.warning("Yandex ASR v3 вернул %d, word-level timestamps недоступны", response.status_code)
                return None

            operation_id = response.json().get("id")
            if not operation_id:
                logger.warning("Yandex ASR v3 не вернул id операции")
                return None

            words: list[dict] = []
            for _ in range(60):  # до ~120 сек (по 2 сек на попытку)
                await asyncio.sleep(2)
                res = await client.get(
                    ASR_V3_RESULT_URL,
                    params={"operation_id": operation_id},
                    headers=headers,
                )
                if res.status_code != 200:
                    logger.warning("Yandex ASR v3 getRecognition вернул %d, прерываю ожидание", res.status_code)
                    return None
                words = _parse_asr_v3_result(res.text)
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


def _parse_asr_v3_result(raw: str) -> list[dict]:
    """Разбор потокового ответа ASR v3 (NDJSON): собираем слова из final-чанков."""
    words: list[dict] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = obj.get("result") or {}
        final = result.get("final") or {}
        for alt in final.get("alternatives", []):
            for w in alt.get("words", []):
                text_w = (w.get("text") or "").strip()
                if not text_w:
                    continue
                words.append({
                    "word": text_w,
                    "start": int(w.get("startTimeMs", 0)) / 1000.0,
                    "end": int(w.get("endTimeMs", 0)) / 1000.0,
                })
    return words


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


async def _synthesize_chunk(client: httpx.AsyncClient, chunk: str, dest: Path,
                            voice: str | None = None, speed: float | None = None) -> None:
    params = {
        "text": chunk,
        "lang": config.TTS_LANG,
        "voice": voice or config.TTS_VOICE,
        "emotion": config.TTS_EMOTION,
        "speed": str(speed if speed is not None else config.TTS_SPEED),
        "format": "lpcm",
        "sampleRateHertz": str(config.TTS_SAMPLE_RATE),
    }
    if config.YANDEX_FOLDER_ID:
        params["folderId"] = config.YANDEX_FOLDER_ID

    async def _do() -> None:
        response = await client.post(TTS_URL, params=params, headers=_auth_headers())
        if response.status_code != 200:
            if is_retryable_status(response.status_code):
                raise RetryableError(f"TTS status {response.status_code}", response.status_code)
            try:
                err_detail = response.json().get("error", {}).get("message", "")
            except Exception:
                err_detail = ""
            status_messages = {
                401: "Неверный API-ключ Yandex SpeechKit. Проверьте TELEGRAM_BOT_TOKEN и YANDEX_API_KEY.",
                403: "Доступ к SpeechKit запрещён. Проверьте тариф и доступность сервиса.",
            }
            user_msg = status_messages.get(response.status_code, "Ошибка синтеза речи. Попробуйте позже.")
            if err_detail:
                user_msg += f" ({err_detail})"
            raise APIError("Yandex SpeechKit", user_msg, response.status_code)
        dest.write_bytes(response.content)

    try:
        await retry_async(_do, retries=3, base_delay=1.0)
    except RetryableError as exc:
        raise APIError(
            "Yandex SpeechKit",
            "Сервис SpeechKit временно недоступен. Попробуйте позже.",
            exc.status_code,
        ) from exc


async def synthesize(text: str, work_dir: Path, voice: str | None = None, speed: float | None = None) -> tuple[Path, float]:
    """Синтез речи. Возвращает (путь к mp3, длительность в секундах).

    voice/speed — переопределения настроек пользователя (если заданы, берутся
    вместо значений из config).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    chunks = split_into_chunks(text)
    pcm_paths: list[Path] = []
    async with httpx.AsyncClient(timeout=180) as client:
        for i, chunk in enumerate(chunks):
            logger.info("TTS: чанк %d/%d (%d символов)", i + 1, len(chunks), len(chunk))
            pcm = work_dir / f"tts_{i:03d}.pcm"
            try:
                await _synthesize_chunk(client, chunk, pcm, voice=voice, speed=speed)
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