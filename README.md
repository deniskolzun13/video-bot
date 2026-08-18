# Telegram-бот «Текст → видео с озвучкой и субтитрами»

Принимает от пользователя текст новости и возвращает готовый вертикальный
`.mp4` (1080×1920, 9:16) с озвучкой, видео-подложкой и hardsub-субтитрами —
контент, готовый к публикации в Shorts/Reels/TikTok/Telegram.

## Как это работает

```
текст → Yandex SpeechKit (озвучка) → тайминг фраз → Pexels / Pixabay / Steam (клипы) → ffmpeg (рендер) → .mp4
```

1. **Озвучка** — Yandex SpeechKit (REST API v1), голос `zahar` + эмоция `good`
   (мужской, энергичный; меняется в `.env`). Длинный текст разбивается на чанки
   по границам предложений (лимит SpeechKit ~5000 символов) и склеивается с
   **кроссфейдом** (`TTS_CROSSFADE`, по умолчанию 50 мс) между чанками для
   плавного перехода интонации.
2. **Субтитры** — текст делится на смысловые фразы (предложения склеиваются в
   фразы 20–70 символов). Тайминг по трём уровням точности:
   1. **Word-level timestamps** от Yandex SpeechKit (ASR на синтезированном
      аудио) — самый точный вариант;
   2. **Forced alignment** через whisper-timestamped (CPU, модель `base`);
   3. **Пропорциональный** по доле символов — fallback.
   Стиль: Arial Bold 64px, белый текст, чёрная обводка + тень, снизу экрана.
   Опционально: **выделение ключевых слов и чисел** другим цветом
   (`SUB_HIGHLIGHT_KEYWORDS`) и **karaoke-анимация появления слов**
   (`SUB_KARAOKE`, требует word-level timestamps).
3. **Видео-подложка** — три источника (`VIDEO_SOURCE` в `.env`):
   - **Steam** (для игровых новостей): название игры из текста определяет
     YandexGPT, затем через Steam Store API находится официальный трейлер игры
     (HLS, скачивается ffmpeg'ом) и режется на сегменты под фразы — видео
     гарантированно совпадает с игрой из новости.
   - **Pexels** (сток, для остальных новостей): ключевые слова извлекаются
     гибридно — YandexGPT (английские, конкретные визуальные темы) либо
     частотная эвристика с переводом RU→EN через deep-translator.
   - **Pixabay** (fallback для Pexels): если Pexels вернул меньше
     `MIN_CLIPS_PER_PHRASE` клипов на тему — автоматически подключается Pixabay.
   Ключевые темы извлекаются **на весь текст новости** (3–5 тем), клипы
   подбираются по релевантности к фразе, а не по кругу. Клипы с разрешением
   ниже `MIN_CLIP_WIDTH` (720px по умолчанию) отсекаются — чтобы не было мыла
   после растягивания до 1080×1920. На каждую фразу — свой сегмент, приведённый
   к 9:16: по умолчанию размытая подложка по бокам (`VIDEO_PADDING=blur` —
   видно весь кадр, важно для трейлеров 16:9), при желании жёсткий кроп по
   центру (`VIDEO_PADDING=crop`). Для Steam-трейлеров выбирается самый
   информативный ролик (highlight/геймплей в приоритете, тизеры и патчи
   отсекаются), на случай лимитов Steam API есть ретраи.
4. **Кэш** — локальный дисковый кэш скачанных клипов и Steam-трейлеров
   (`cache/`, TTL 7 дней, LRU при превышении 500 МБ). Повторные запуски
   мгновенно берут клипы из кэша. Команда `/clear_cache` для очистки.
5. **Фоновая музыка** — опционально (`BACKGROUND_MUSIC=true`): тихая музыка
   из `music/` подмешивается через ffmpeg со **sidechain-компрессором**,
   который приглушает её, пока говорит диктор.
6. **Рендер** — ffmpeg: склейка клипов встык под длительность аудио, наложение
   озвучки и субтитров (hardsub через `ass=`-фильтр, нужен ffmpeg с libass).
   Параллельный рендер до `RENDER_CONCURRENCY` задач одновременно (по умолчанию 2),
   изоляция временных файлов через уникальный `task_id`.
7. **Длинные тексты** — до 3500 символов один ролик; до 14000 символов текст
   разбивается на несколько роликов (до 4), каждый отправляется отдельно.
   Если ролик длиннее `VIDEO_DURATION_WARN_THRESHOLD` (45 с по умолчанию) —
   бот пришлёт мягкое предупреждение, что для Shorts/Reels лучше 20–40 с.

## Требования

- Linux (ПК пользователя), Python 3.11+
- `ffmpeg` с libass (стандартные сборки, например из apt, его содержат)

## Установка

### 1. ffmpeg

```bash
# Debian / Ubuntu / Mint
sudo apt update && sudo apt install -y ffmpeg

# Fedora
sudo dnf install -y ffmpeg

# Arch / Manjaro
sudo pacman -S ffmpeg
```

Проверка: `ffmpeg -version` (в выводе должно быть `--enable-libass`).

Рендер автоматически выбирает H.264-энкодер: `libx264` → `libopenh264` →
`h264_vaapi`/`h264_v4l2m2m` (хватит любого из них). Если нет ни одного —
бот сообщит об ошибке и подскажет, что установить.

### 2. Проект

```bash
cd "ГЕНЕРАЦИЯ ВИДЕО"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Ключи API — файл `.env`

```bash
cp .env.example .env
```

Заполни:

| Переменная | Где взять |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` | [console.yandex.cloud](https://console.yandex.cloud) → сервис SpeechKit. Либо `YANDEX_IAM_TOKEN` (живёт 12 часов) вместо ключа |
| `PEXELS_API_KEY` | [pexels.com/api](https://www.pexels.com/api/) (бесплатно) |
| `PIXABAY_API_KEY` | [pixabay.com/api/docs](https://pixabay.com/api/docs/) (бесплатно, fallback для Pexels) |

Опционально: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` — для извлечения
ключевых слов через LLM (любой OpenAI-совместимый API: YandexGPT, OpenAI, OpenRouter).
Без них работает эвристика.

### 4. Запуск

```bash
source .venv/bin/activate
python bot.py
```

Напиши боту текст новости (или пришли `.txt`) — получишь видео.

## Настройка озвучки (`.env`)

```ini
TTS_VOICE=zahar        # голос
TTS_EMOTION=good       # амплуа: у zahar доступны только neutral/good (evil — ошибка API)
TTS_SPEED=1.0          # скорость 0.1–3.0
TTS_LANG=ru-RU
SUB_FONT=Arial         # на Linux Arial подставится из Liberation Sans
```

## Все переменные `.env`

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Токен бота (обязательно) |
| `ALLOWED_USER_ID` | — | ID владельца (единственный доступ) |
| `YANDEX_API_KEY` / `YANDEX_IAM_TOKEN` | — | Ключ SpeechKit (или IAM-токен) |
| `YANDEX_FOLDER_ID` | — | Каталог Yandex Cloud |
| `PEXELS_API_KEY` | — | Ключ Pexels (сток) |
| `PIXABAY_API_KEY` | — | Ключ Pixabay (fallback для Pexels) |
| `VIDEO_SOURCE` | `auto` | `auto` / `steam` / `pexels` |
| `MIN_CLIPS_PER_PHRASE` | `2` | Минимум клипов на фразу из Pexels, иначе Pixabay fallback |
| `MIN_CLIP_WIDTH` | `720` | Мин. ширина клипа (фильтр «мыла») |
| `CACHE_DIR` | `cache` | Директория кэша клипов |
| `CACHE_TTL_DAYS` | `7` | TTL кэша |
| `MAX_CACHE_SIZE_MB` | `500` | Лимит размера кэша (LRU) |
| `RENDER_CONCURRENCY` | `2` | Параллельный рендер |
| `VIDEO_PADDING` | `blur` | `blur` — размытая подложка, `crop` — кроп |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | LLM для ключевых слов (YandexGPT, OpenAI…) |
| `BACKGROUND_MUSIC` | `false` | Вкл/выкл фоновую музыку |
| `BG_MUSIC_PATH` | `music/ambience.mp3` | Путь к музыкальному файлу |
| `BG_MUSIC_VOLUME` | `-23` | Громкость музыки в dB (приглушение sidechain) |
| `TTS_VOICE` / `TTS_EMOTION` / `TTS_SPEED` / `TTS_LANG` | — | Параметры голоса |
| `TTS_MAX_CHUNK` | `4500` | Лимит символов на чанк TTS |
| `TTS_CROSSFADE` | `0.05` | Кроссфейд между чанками (сек) |
| `TTS_DISABLE_CROSSFADE` | — | Отключить кроссфейд |
| `SUB_FONT` | `Arial` | Шрифт субтитров |
| `SUB_HIGHLIGHT_KEYWORDS` | `false` | Выделять ключевые слова/числа цветом |
| `SUB_HIGHLIGHT_COLOR` | `&H0000FF&` | Цвет выделения (ASS BGR) |
| `SUB_KARAOKE` | `false` | Karaoke-анимация слов (нужны word-level timestamps) |
| `MAX_VIDEO_SYMBOLS` | `3500` | Макс. символов на ролик |
| `MAX_PARTS` | `4` | Макс. число роликов |
| `MAX_VIDEO_DURATION` | `75` | Макс. длительность озвучки (сек) |
| `VIDEO_DURATION_WARN_THRESHOLD` | `45` | Порог предупреждения о длинном ролике |

Послушать голоса: [страница SpeechKit](https://yandex.cloud/ru/services/speechkit),
вкладка «Синтез речи» в консоли, или [AI Studio](https://aistudio.yandex.ru/ai-speech).

## Структура проекта

```
bot.py            # точка входа, обработчики Telegram, очередь задач
pipeline.py       # оркестратор пайплайна, разбивка длинных текстов
tts.py            # синтез речи (SpeechKit v1), word-level ASR, склейка с кроссфейдом
subtitles.py      # фразы, тайминг (word-level → alignment → fallback), .ass/.srt, highlight, karaoke
prompts.py        # промпты для YandexGPT (вынесены из video_source.py)
video_source.py   # VideoSourceProvider: PexelsProvider + PixabayProvider + SteamProvider
video_render.py   # сборка финального видео через ffmpeg (+ фоновая музыка)
config.py         # чтение .env, константы
cache.py          # дисковый кэш клипов (TTL, LRU)
tests/            # тесты для subtitles.py
requirements.txt  # основные зависимости
requirements-dev.txt # dev-зависимости (pytest)
```

## Известные упрощения (осознанные решения)

- **Ключевые слова** — эвристика даёт русские слова «как есть» (Pexels лучше
  ищет по-английски); с подключённым LLM он переводит их в английские теги.
- **Тайминг субтитров** — word-level (ASR) или forced alignment; если оба
  недоступны (нет ключа / нет whisper) — пропорциональный по доле символов.
- **Видео по фразам** — на каждую фразу отдельный клип, подобранный по
  релевантности к глобальным темам новости. Для текстов короче 3 фраз —
  простая логика (клипы по кругу между ключевыми словами).
- Озвучка по частям: каждый чанк синтезируется отдельно (рекомендация
  Yandex — синтезировать текст целиком; кроссфейд сглаживает стыки).

## Ограничения и лимиты

- Озвучка короче `MAX_VIDEO_DURATION` (75 с по умолчанию) — чтобы ролик
  влезал в лимиты Shorts/Reels.
- Текст до 14 000 символов (4 ролика по 3500). Дольше — вежливый отказ.
- Параллельный рендер до `RENDER_CONCURRENCY` задач одновременно — бот не зависает,
  очередь на `asyncio.Semaphore`. Временные файлы изолированы через `task_id`.

## Команды бота

- `/start` — справка
- `/help` — справка
- `/clear_cache` — полная очистка кэша клипов (только для владельца)

## Разработка

```bash
# Установка dev-зависимостей
pip install -r requirements-dev.txt

# Запуск тестов
pytest tests/
```