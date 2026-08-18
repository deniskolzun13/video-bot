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

# Кэш скачанных клипов
CACHE_DIR = getenv("CACHE_DIR", "cache")
CACHE_TTL_DAYS = int(getenv("CACHE_TTL_DAYS", "7"))
MAX_CACHE_SIZE_MB = int(getenv("MAX_CACHE_SIZE_MB", "500"))

# Параллельный рендер
RENDER_CONCURRENCY = int(getenv("RENDER_CONCURRENCY", "2"))

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

LLM_API_KEY = getenv("LLM_API_KEY")
LLM_BASE_URL = getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = getenv("LLM_MODEL", "gpt-4o-mini")

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
MAX_VIDEO_DURATION = float(getenv("MAX_VIDEO_DURATION", "75"))
MAX_VIDEO_SYMBOLS = int(getenv("MAX_VIDEO_SYMBOLS", "3500"))
MAX_PARTS = int(getenv("MAX_PARTS", "4"))

KEYWORDS_COUNT = int(getenv("KEYWORDS_COUNT", "4"))

SUB_FONT = getenv("SUB_FONT", "Arial")
SUB_FONTSIZE = 64
SUB_PRIMARY = "&H00FFFFFF"
SUB_OUTLINE_COLOR = "&H00000000"
SUB_OUTLINE_WIDTH = 5
SUB_SHADOW = 2
SUB_MARGIN_V = 100

WORK_DIR = getenv("WORK_DIR", "work")
OUTPUT_DIR = getenv("OUTPUT_DIR", "output")

BOT_CONCURRENCY = 1