import os
import socket

from dotenv import load_dotenv

load_dotenv()


def force_ipv4() -> None:
    """Принудительный IPv4: у части провайдеров IPv6-маршрут битый
    (TLS обрывается на рукопожатии, "unexpected eof while reading")."""
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == socket.AF_UNSPEC:
            family = socket.AF_INET
        return original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = ipv4_getaddrinfo


force_ipv4()


def getenv(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


TELEGRAM_BOT_TOKEN = getenv("TELEGRAM_BOT_TOKEN")

ALLOWED_USER_ID = getenv("ALLOWED_USER_ID")

YANDEX_API_KEY = getenv("YANDEX_API_KEY")
YANDEX_IAM_TOKEN = getenv("YANDEX_IAM_TOKEN")
YANDEX_FOLDER_ID = getenv("YANDEX_FOLDER_ID")

PEXELS_API_KEY = getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = getenv("PIXABAY_API_KEY")

VIDEO_SOURCE = getenv("VIDEO_SOURCE", "auto")
MIN_CLIPS_PER_PHRASE = int(getenv("MIN_CLIPS_PER_PHRASE", "2"))
# Минимальное разрешение клипов (не ниже этого — будет мыльно)
MIN_CLIP_WIDTH = int(getenv("MIN_CLIP_WIDTH", "720"))

# Кэш скачанных клипов
CACHE_DIR = getenv("CACHE_DIR", "cache")
CACHE_TTL_DAYS = int(getenv("CACHE_TTL_DAYS", "7"))
MAX_CACHE_SIZE_MB = int(getenv("MAX_CACHE_SIZE_MB", "500"))

# Параллельный рендер
RENDER_CONCURRENCY = int(getenv("RENDER_CONCURRENCY", "2"))
# Таймаут одного ffmpeg-рендера (сек)
RENDER_TIMEOUT_SECONDS = float(getenv("RENDER_TIMEOUT_SECONDS", "600"))

# Как приводить клипы к 9:16: blur — размытая подложка по бокам (видно весь кадр,
# для трейлеров 16:9), crop — жёсткая обрезка по центру
VIDEO_PADDING = getenv("VIDEO_PADDING", "blur")

TTS_VOICE = getenv("TTS_VOICE", "zahar")
TTS_EMOTION = getenv("TTS_EMOTION", "good")
TTS_SPEED = float(getenv("TTS_SPEED", "1.0"))
TTS_LANG = getenv("TTS_LANG", "ru-RU")
TTS_SAMPLE_RATE = 48000
TTS_MAX_CHUNK = int(getenv("TTS_MAX_CHUNK", "4500"))
# Кроссфейд между чанками TTS в секундах (убирает слышимый шов интонации)
TTS_CROSSFADE = float(getenv("TTS_CROSSFADE", "0.05"))
# Отключить кроссфейд (склейка внахлёст) — для отладки/контроля
TTS_DISABLE_CROSSFADE = getenv("TTS_DISABLE_CROSSFADE", "").lower() in ("1", "true", "yes")

# --- LLM (AI provider abstraction) ---
# LLM_PROVIDER: openai (OpenAI-совместимый API: OpenAI, OpenRouter, YandexGPT, ...)
LLM_PROVIDER = getenv("LLM_PROVIDER", "openai")
LLM_API_KEY = getenv("LLM_API_KEY")
LLM_BASE_URL = getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = getenv("LLM_MODEL", "gpt-4o-mini")
# Способ авторизации: bearer (Authorization: Bearer <key>) или
# api-key (Authorization: Api-Key <key>). По умолчанию auto — определяется
# по префиксу ключа (sk-/t1. -> Bearer). Имя заголовка задаёт LLM_AUTH_HEADER.
LLM_AUTH_TYPE = getenv("LLM_AUTH_TYPE", "auto").lower()
LLM_AUTH_HEADER = getenv("LLM_AUTH_HEADER", "Authorization")
# Таймаут и ретраи для LLM-запросов
LLM_TIMEOUT = float(getenv("LLM_TIMEOUT", "60"))
LLM_RETRIES = int(getenv("LLM_RETRIES", "2"))

# --- Data storage ---
DATA_DIR = getenv("DATA_DIR", "data")
DB_PATH = getenv("DB_PATH", f"{DATA_DIR}/video_bot.db")

# --- Кэш (v2) ---
CACHE_ENABLED = getenv("CACHE_ENABLED", "true").lower() in ("1", "true", "yes")

# --- Тайминги / forced alignment ---
# Максимальное время транскрипции whisper (сек). По истечении — proportional fallback.
ALIGNMENT_TIMEOUT_SECONDS = float(getenv("ALIGNMENT_TIMEOUT_SECONDS", "30"))

# --- Сценарий / сцены ---
# SCRIPT_GENERATION: off — использовать текст как есть (по умолчанию, не переписываем
#   пользовательский текст), on — генерировать hook/body/ending из новости
SCRIPT_GENERATION = getenv("SCRIPT_GENERATION", "off").lower()
# Число сцен для сценарного плана
SCENES_MAX = int(getenv("SCENES_MAX", "12"))

# --- Стиль субтитров ---
# classic / tiktok / news / gaming / minimal
SUBTITLE_STYLE = getenv("SUBTITLE_STYLE", "tiktok")

# --- Fallback на изображения ---
# IMAGE_FALLBACK=true — если видео не найдено, использовать стоковые изображения
IMAGE_FALLBACK = getenv("IMAGE_FALLBACK", "true").lower() in ("1", "true", "yes")
# Секунд на одно изображение при Ken Burns-эффекте
IMAGE_KEN_BURNS = float(getenv("IMAGE_KEN_BURNS", "4.0"))

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30


def resolve_video_size(video_format: str | None) -> tuple[int, int]:
    """Разрешает формат в (width, height).

    vertical   -> 1080x1920 (9:16, Shorts/Reels)
    square     -> 1080x1080 (1:1)
    landscape  -> 1920x1080 (16:9)
    """
    fmt = (video_format or "vertical").lower()
    if fmt == "square":
        return 1080, 1080
    if fmt == "landscape":
        return 1920, 1080
    return 1080, 1920  # vertical / default
MAX_VIDEO_DURATION = float(getenv("MAX_VIDEO_DURATION", "75"))
MAX_VIDEO_SYMBOLS = int(getenv("MAX_VIDEO_SYMBOLS", "3500"))
MAX_PARTS = int(getenv("MAX_PARTS", "4"))
# Порог для предупреждения о длинном ролике (для удержания attention)
VIDEO_DURATION_WARN_THRESHOLD = float(getenv("VIDEO_DURATION_WARN_THRESHOLD", "45"))

KEYWORDS_COUNT = int(getenv("KEYWORDS_COUNT", "4"))

# Фоновая музыка
BACKGROUND_MUSIC = getenv("BACKGROUND_MUSIC", "").lower() in ("1", "true", "yes")
BG_MUSIC_PATH = getenv("BG_MUSIC_PATH", "music/ambience.mp3")
BG_MUSIC_VOLUME = float(getenv("BG_MUSIC_VOLUME", "-23"))  # dB, относительно громкости диалога

SUB_FONT = getenv("SUB_FONT", "Arial")
SUB_FONTSIZE = 64
SUB_PRIMARY = "&H00FFFFFF"
SUB_OUTLINE_COLOR = "&H00000000"
SUB_OUTLINE_WIDTH = 5
SUB_SHADOW = 2
SUB_MARGIN_V = 100
# Выделение ключевых слов другим цветом в субтитрах (\c&H...&)
SUB_HIGHLIGHT_KEYWORDS = getenv("SUB_HIGHLIGHT_KEYWORDS", "").lower() in ("1", "true", "yes")
SUB_HIGHLIGHT_COLOR = getenv("SUB_HIGHLIGHT_COLOR", "&H0000FF&")
# Karaoke-анимация появления слов по буквам (\k-теги в ASS)
SUB_KARAOKE = getenv("SUB_KARAOKE", "").lower() in ("1", "true", "yes")

WORK_DIR = getenv("WORK_DIR", "work")
OUTPUT_DIR = getenv("OUTPUT_DIR", "output")
# Директория для артефактов job: data/jobs/<JOB_ID>/
JOB_DIR = getenv("JOB_DIR", f"{DATA_DIR}/jobs")

# Сколько заданий (генераций) может выполняться параллельно.
# Отдельно от рендера: рендер (ffmpeg) лимитируется RENDER_CONCURRENCY.
JOB_CONCURRENCY = int(getenv("JOB_CONCURRENCY", "2"))


def validate_config() -> list[str]:
    """Проверка критичной конфигурации при старте. Возвращает список ошибок.

    Ошибки не прерывают импорт (бот может запуститься с дефолтами), но
    должны быть показаны в логах. Ключи: TELEGRAM_BOT_TOKEN обязателен;
    PEXELS_API_KEY обязателен (т.к. pexels — основной источник видео);
    LLM_API_KEY важен, но бот работает и без него (fallback-эвристика).
    """
    errors: list[str] = []

    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN не задан (обязателен)")

    source = VIDEO_SOURCE
    if source in ("pexels", "auto") and not PEXELS_API_KEY:
        errors.append("PEXELS_API_KEY не задан — стоковые видео работать не будут")

    if source == "steam" and not (YANDEX_API_KEY or YANDEX_IAM_TOKEN):
        errors.append("VIDEO_SOURCE=steam, но не заданы YANDEX_API_KEY/YANDEX_IAM_TOKEN")

    if not (YANDEX_API_KEY or YANDEX_IAM_TOKEN):
        errors.append("YANDEX_API_KEY / YANDEX_IAM_TOKEN не заданы — TTS и ASR не будут работать")

    if CACHE_ENABLED and MAX_CACHE_SIZE_MB <= 0:
        errors.append("MAX_CACHE_SIZE_MB должно быть > 0")

    if JOB_CONCURRENCY < 1 or RENDER_CONCURRENCY < 1:
        errors.append("JOB_CONCURRENCY и RENDER_CONCURRENCY должны быть >= 1")

    return errors