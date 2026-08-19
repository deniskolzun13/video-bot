# Telegram-бот «Текст → видео с озвучкой и субтитрами»

Принимает от пользователя текст новости и возвращает готовый вертикальный
`.mp4` (1080×1920, 9:16) с озвучкой, видео-подложкой и hardsub-субтитрами —
контент, готовый к публикации в Shorts/Reels/TikTok/Telegram.

## Как это работает

```
текст → [анализ] → [сценарий?] → [сцены?] → Yandex SpeechKit (озвучка) → тайминг фраз
  → ранжирование видео (Pexels / Pixabay / Steam) → субтитры → ffmpeg (рендер) → валидация → .mp4
```

1. **Анализ текста** (`script/analyzer.py`) — извлекаются тема, сущности и
   визуальные ключевые слова. Если задан ключ LLM — через OpenAI-совместимый API
   (провайдер `ai/`), иначе детерминированная эвристика с переводом RU→EN.
2. **Сценарий (опционально)** — при `SCRIPT_GENERATION=on` LLM переписывает текст
   в структуру «хук → тело → концовка» для удержания внимания. Иначе используется
   исходный текст.
3. **Сцены** (`script/scene_planner.py`) — при `SCENES_MAX` текст делится на сцены
   с визуальной темой и таймингом; fallback — фразы из текста.
4. **Озвучка** — Yandex SpeechKit (REST API v1), голос `zahar` + эмоция `good`
   (мужской, энергичный; меняется в `.env`). Длинный текст разбивается на чанки
   по границам предложений (лимит SpeechKit ~5000 символов) и склеивается с
   **кроссфейдом** (`TTS_CROSSFADE`, по умолчанию 50 мс) между чанками для
   плавного перехода интонации. Транзиентные ошибки API (429/5xx) ретраятся
   автоматически (`utils/retry.py`).
5. **Субтитры** — текст делится на смысловые фразы (предложения склеиваются в
   фразы 20–70 символов). Тайминг по трём уровням точности:
   1. **Word-level timestamps** от Yandex SpeechKit (ASR v3 `recognizeFileAsync`
      на синтезированном аудио) — самый точный вариант;
   2. **Forced alignment** через whisper-timestamped (CPU, модель `base`);
   3. **Пропорциональный** по доле символов — fallback.
   Стиль выбирается пресетом (`subtitles/styles.py`): `classic`, `tiktok`, `news`,
   `gaming`, `minimal`. Опционально: **выделение ключевых слов и чисел** другим
   цветом (`SUB_HIGHLIGHT_KEYWORDS`) и **karaoke-анимация появления слов**
   (`SUB_KARAOKE`, требует word-level timestamps).
6. **Подбор видео** (`video/selector.py`) — кандидаты ранжируются (`video/ranking.py`)
   по ориентации, разрешению, длительности и совпадению keywords; уже
   использованные клипы получают жёсткий штраф (защита от повторов). Источники
   (`VIDEO_SOURCE` в `.env`):
   - **Steam** (для игровых новостей): название игры из текста определяет LLM,
     затем через Steam Store API находится официальный трейлер игры
     (HLS, скачивается ffmpeg'ом) и режется на сегменты под фразы — видео
     гарантированно совпадает с игрой из новости.
   - **Pexels** (сток, для остальных новостей): ключевые слова извлекаются
     гибридно — LLM (английские, конкретные визуальные темы) либо частотная
     эвристика с переводом RU→EN через deep-translator.
   - **Pixabay** (fallback для Pexels): если Pexels вернул меньше
     `MIN_CLIPS_PER_PHRASE` клипов на тему — автоматически подключается Pixabay.
   Если ни один клип не найден — генерируется анимированный градиент
   (`video/fallback.py`, `IMAGE_FALLBACK=true`). Клипы с разрешением ниже
   `MIN_CLIP_WIDTH` (720px по умолчанию) отсекаются. На каждую фразу — свой
   сегмент, приведённый к 9:16: по умолчанию размытая подложка по бокам
   (`VIDEO_PADDING=blur`), при желании жёсткий кроп (`VIDEO_PADDING=crop`).
7. **Кэш** — sha256-кэш скачанных клипов (`video/cache.py`, `CACHE_ENABLED=true`)
   и локальный дисковый кэш Steam-трейлеров (`cache/`, TTL 7 дней, LRU при
   превышении 500 МБ). Повторные запуски мгновенно берут клипы из кэша.
   Команда `/clear_cache` для очистки.
8. **Фоновая музыка** — опционально (`BACKGROUND_MUSIC=true`): тихая музыка
   из `music/` подмешивается через ffmpeg со **sidechain-компрессором**,
   который приглушает её, пока говорит диктор.
9. **Рендер и валидация** — ffmpeg: склейка клипов встык под длительность аудио,
   наложение озвучки и субтитров (hardsub через `ass=`-фильтр, нужен ffmpeg с
   libass). После сборки результат проверяется (`video_render.validate_output`):
   существование файла, наличие видео- и аудио-потоков, разрешение 1080×1920,
   ненулевая длительность. Параллельный рендер до `RENDER_CONCURRENCY` задач
   (по умолчанию 2), изоляция временных файлов через уникальный `task_id`.
10. **История** — каждая генерация сохраняется в SQLite (`storage/`, `data/video_bot.db`)
    с уникальным `job_id` (JOB-ДАТА-NNNN), статусом, длительностью и путём.
    Раздел «📚 История» в боте показывает последние генерации.
11. **Длинные тексты** — до 3500 символов один ролик; до 14000 символов текст
    разбивается на несколько роликов (до 4), каждый отправляется отдельно.
    Если ролик длиннее `VIDEO_DURATION_WARN_THRESHOLD` (45 с по умолчанию) —
    бот пришлёт мягкое предупреждение.

## Что нового в v2.0.1 (hardening)

- **Отмена задач** — любую генерацию можно остановить кнопкой «❌ Отмена»:
  проверка токена отмены на каждом этапе, ffmpeg-процесс завершается
  принудительно (`terminate`/`kill`), статус в истории — `cancelled`.
- **Надёжный рендер** — таймаут `RENDER_TIMEOUT_SECONDS` (600 с) на один рендер;
  очередь рендеров ограничена `RENDER_CONCURRENCY` отдельно от общего числа
  генераций `JOB_CONCURRENCY`.
- **Формат видео** — в настройках бота можно выбрать `вертикальный (9:16)`,
  `квадратный (1:1)` или `горизонтальный (16:9)`; валидация проверяет нужное
  разрешение.
- **Артефакты заданий** — каждый job хранит файлы в
  `data/jobs/<JOB_ID>/{input,tts,video,subtitles,output}` — удобно разбирать
  проблемные генерации.
- **Умный подбор видео** — прозрачное ранжирование по весам (релевантность,
  ориентация, разрешение, длительность, keywords), защита от повторов,
  автоматический fallback Pexels → Pixabay и фоновый градиент.
- **Точный тайминг** — word-level тайминги от Yandex ASR, forced alignment через
  whisper с таймаутом (`ALIGNMENT_TIMEOUT_SECONDS`), нормализация и нечёткое
  сопоставление слов.
- **Надёжный LLM** — ретраи на 408/429/5xx, поддержка авторизации
  (`auto`/`bearer`/`api-key`), робастный парсер JSON-ответов.
- **Логирование** — каждая строка лога помечается текущим `job_id`, при старте
  проверяется конфигурация (`validate_config`), в истории видно, если файл
  больше недоступен.

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
| `CACHE_ENABLED` | `true` | Sha256-кэш скачанных клипов (dedup) |
| `CACHE_DIR` | `cache` | Директория кэша клипов |
| `CACHE_TTL_DAYS` | `7` | TTL кэша |
| `MAX_CACHE_SIZE_MB` | `500` | Лимит размера кэша (LRU) |
| `RENDER_CONCURRENCY` | `2` | Параллельный рендер |
| `VIDEO_PADDING` | `blur` | `blur` — размытая подложка, `crop` — кроп |
| `LLM_PROVIDER` | `openai` | Провайдер LLM (сейчас только `openai`) |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | LLM для анализа текста/ключевых слов (YandexGPT, OpenAI…) |
| `LLM_TIMEOUT` / `LLM_RETRIES` | `60` / `2` | Таймаут и ретраи LLM |
| `SCRIPT_GENERATION` | `off` | `on`/`off`/`auto` — генерация сценария (хук/тело/концовка) |
| `SCENES_MAX` | `12` | Максимум сцен в ролике |
| `SUBTITLE_STYLE` | `tiktok` | `classic`/`tiktok`/`news`/`gaming`/`minimal` |
| `DATA_DIR` | `data` | Директория БД |
| `DB_PATH` | `data/video_bot.db` | Файл SQLite (пользователи, история) |
| `IMAGE_FALLBACK` | `true` | Fallback-фон (анимированный градиент), если видео не найдено |
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
bot.py               # точка входа, обработчики Telegram, inline-клавиатуры, настройки, отмена
pipeline.py          # оркестратор пайплайна, разбивка длинных текстов
config.py            # чтение .env, константы
tts.py               # синтез речи (SpeechKit v1), word-level ASR (v3), склейка с кроссфейдом
video_source.py      # VideoSourceProvider: PexelsProvider + PixabayProvider + SteamProvider
video_render.py      # сборка финального видео через ffmpeg, probe_video, validate_output
prompts.py           # промпты для LLM (вынесены из video_source.py)
cache.py             # дисковый кэш клипов (TTL, LRU)

ai/                  # провайдер LLM: base.py, openai_compat.py, factory.py
script/              # analyzer.py (анализ текста), generator.py (сценарий), scene_planner.py (сцены)
video/               # ranking.py, selector.py, downloader.py, cache.py (sha256), fallback.py
subtitles/           # generator.py (.ass/.srt), alignment.py (тайминг), styles.py (пресеты)
storage/             # database.py (SQLite: users/jobs/videos), history.py (история генераций)
utils/               # retry.py (ретраи с backoff), hashing.py (sha256), cleanup.py (очистка)

tests/               # тесты (pytest)
data/                # SQLite-БД (создаётся автоматически)
requirements.txt     # основные зависимости
requirements-dev.txt # dev-зависимости (pytest, ruff)
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

Плюс inline-клавиатура: **🎬 Создать видео**, **📚 История**, **⚙️ Настройки**
(голос, скорость, источник видео, стиль субтитров — сохраняются в SQLite),
**ℹ️ Помощь**. Во время генерации — единый статус-трейсер с кнопкой **❌ Отменить**.

## Разработка

```bash
# Установка dev-зависимостей
pip install -r requirements-dev.txt

# Запуск тестов
pytest tests/

# Линтер
ruff check .
```