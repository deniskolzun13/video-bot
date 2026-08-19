# Telegram-бот «Текст → видео с озвучкой и субтитрами»

Принимает от пользователя текст новости и возвращает готовый вертикальный
`.mp4` (1080×1920, 9:16) с озвучкой, видео-подложкой и hardsub-субтитрами —
контент, готовый к публикации в Shorts/Reels/TikTok/Telegram.

## Два режима работы

Бот умеет работать в двух режимах (`AI_MODE` в `.env`):

- **`local` (по умолчанию)** — полностью локальный AI: Ollama (LLM), локальный
  TTS (espeak-ng/piper), локальный Whisper (ASR), SQLite. **Никакого облачного
  fallback** — весь текст новостей остаётся на вашей машине. Подходит для
  NVIDIA RTX 3050 (8 ГБ) и 16 ГБ ОЗУ.
- **`cloud`** — облачные сервисы (Yandex SpeechKit для озвучки, OpenAI-совместимый
  LLM, Pexels/Pixabay для видео).

Кроме обычного режима «один текст → одно видео», в **local-режиме** доступен
**выпуск из нескольких новостей** (кнопка «📰 Создать выпуск из новостей»):
до `MAX_NEWS_PER_BATCH` новостей обрабатываются локальной LLM (редактирование,
дедупликация, сортировка, вступление/переходы/завершение), озвучиваются локальным
TTS и собираются в **один MP4** с переходами и субтитрами.

## Локальный AI (режим `local`)

```
несколько новостей
  → локальная LLM (Ollama): редактирование, дедупликация, порядок, intro/outro, переходы
  → локальный TTS (espeak-ng/piper) каждого сегмента
  → локальный Whisper (word-level тайминг субтитров)
  → видео (локальная библиотека data/media → онлайн-сток)
  → UnifiedTimeline (intro/news/transition/outro)
  → ОДИН FFmpeg render (crossfade) → ОДИН .mp4
```

### Установка Ollama и модели

```bash
# 1. Установить Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Запустить сервис
systemctl --user enable --now ollama   # или: ollama serve

# 3. Скачать модель (qwen3:8b — ~5 ГБ)
ollama pull qwen3:8b

# 4. Проверить, что Ollama работает
curl http://127.0.0.1:11434/api/tags

# 5. Проверить GPU (видеокарта должна быть видна Ollama)
nvidia-smi
```

Модель не зашита в код — она задаётся переменной `OLLAMA_MODEL` в `.env`.
Поменять модель на другую (например `qwen3:14b`) можно без изменения кода:
скачать `ollama pull qwen3:14b` и обновить `OLLAMA_MODEL` в `.env`.

### Локальный TTS

- **`espeak-ng`** (по умолчанию) — установите пакет: `sudo apt install espeak-ng`
  (Debian/Ubuntu) или `sudo pacman -S espeak-ng` (Arch). Проверка: `espeak-ng -v ru`.
- **`piper`** — более качественный голос; если установлен, укажите
  `LOCAL_TTS_ENGINE=piper` и положите модель голоса (`.onnx`) в папку проекта.

### Локальный Whisper (ASR)

`whisper-timestamped` даёт word-level тайминги для точных субтитров. Устанавливается
из GitHub (на PyPI его нет):

```bash
pip install -r requirements-optional.txt
```

Модель `base` грузится один раз и кэшируется (потокобезопасный singleton).

### Локальная библиотека видео (`data/media`)

Для `VIDEO_SOURCE=local` положите готовые `.mp4` клипы в папки по темам:

```
data/media/
├── ai/           # нейросети, роботы, чипы
├── technology/   # гаджеты, электроника
├── smartphones/  # смартфоны
├── computers/    # компьютеры, ПК
├── science/      # наука, космос
├── business/     # финансы, стартапы
└── generic/      # общие фоны
```

Поиск идёт по имени файла и имени папки. `VIDEO_SOURCE=auto` использует
локальную библиотеку, если она есть, иначе онлайн-сток; если онлайн недоступен —
снова локальная библиотека.

### Безопасность в локальном режиме

- LLM-провайдер может обращаться **только** к `127.0.0.1`/`localhost` —
  внешний URL вызывает ошибку конфигурации (`ConfigurationError`).
- Нет облачного fallback для LLM/TTS/ASR: если Ollama выключена — бот
  не запустится и сообщит понятную ошибку.
- Полный текст новостей не пишется в INFO-логи; API-ключи и заголовки
  авторизации не логируются.

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
   Если ни один клип не найден — подключается **фото-fallback** с эффектом
   Ken Burns (`IMAGE_FALLBACK=true`): стоковое фото (Pexels/Pixabay) превращается
   в видеоклип с медленным зумом (`IMAGE_KEN_BURNS` — период одного кадра).
   Если и фото нет — генерируется анимированный градиент (`video/fallback.py`).
   Клипы с разрешением ниже
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
  автоматический fallback Pexels → Pixabay, фото с эффектом Ken Burns
  (`IMAGE_FALLBACK`/`IMAGE_KEN_BURNS`) и фоновый градиент.
- **Точный тайминг** — word-level тайминги от Yandex ASR, forced alignment через
  whisper с таймаутом (`ALIGNMENT_TIMEOUT_SECONDS`), нормализация и нечёткое
  сопоставление слов.
- **Надёжный LLM** — ретраи на 408/429/5xx, поддержка авторизации
  (`auto`/`bearer`/`api-key`), робастный парсер JSON-ответов.
- **Логирование** — каждая строка лога помечается текущим `job_id`, при старте
  проверяется конфигурация (`validate_config`), в истории видно, если файл
  больше недоступен.

## Что нового в v2.0.2 (локальный AI / News Batch)

- **Режим `local`** — полностью локальный AI: Ollama (`qwen3:8b`), локальный
  TTS (espeak-ng/piper), локальный Whisper (ASR), SQLite. Без облачного fallback.
- **Выпуск из нескольких новостей** — кнопка «📰 Создать выпуск из новостей»:
  до 20 новостей редактируются локальной LLM, дедуплицируются, сортируются,
  получают вступление/переходы/завершение и собираются в **ОДИН MP4**.
- **UnifiedTimeline** — единый таймлайн (intro/news/transition/outro) с
  абсолютными таймингами, без наложений и разрывов; title card перед новостью.
- **Переходы** — `crossfade` (xfade+acrossfade) и `fade` между сегментами.
- **Локальная видеотека** — `data/media/<категория>` для `VIDEO_SOURCE=local`;
  `auto` = локальная библиотека → онлайн-сток → локальная библиотека.
- **Word-level ASR** — whisper-timestamped (singleton, грузится один раз)
  для точных субтитров с учётом смещения таймлайна.
- **SQLite** — таблицы `news_batches`, `news_items`, `timeline_items` для
  истории выпусков.

## Требования

- Linux (ПК пользователя), Python 3.11+
- `ffmpeg` с libass (стандартные сборки, например из apt, его содержат)
- Для режима `local`: установленный Ollama + скачанная модель
  (см. «Установка Ollama и модели»), локальный TTS-движок (`espeak-ng`),
  `whisper-timestamped` (для word-level субтитров). NVIDIA GPU (RTX 3050 и выше)
  ускоряет Ollama/Whisper; CPU-режим тоже работает, но медленнее.

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
# опционально: локальный Whisper (word-level субтитры в local-режиме)
pip install -r requirements-optional.txt
# dev-зависимости (тесты, линтер)
pip install -e ".[dev]"   # или: pip install -r requirements-dev.txt
```

### 3. Файл `.env`

```bash
cp .env.example .env
```

**Режим `local` (по умолчанию):** достаточно заполнить `TELEGRAM_BOT_TOKEN`
и `ALLOWED_USER_ID`, затем запустить Ollama и скачать модель. Облачные ключи
(Yandex/Pexels/Pixabay) не нужны.

**Режим `cloud`:** заполни:

| Переменная | Где взять |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `YANDEX_API_KEY` + `YANDEX_FOLDER_ID` | [console.yandex.cloud](https://console.yandex.cloud) → сервис SpeechKit. Либо `YANDEX_IAM_TOKEN` (живёт 12 часов) вместо ключа |
| `PEXELS_API_KEY` | [pexels.com/api](https://www.pexels.com/api/) (бесплатно) |
| `PIXABAY_API_KEY` | [pixabay.com/api/docs](https://pixabay.com/api/docs/) (бесплатно, fallback для Pexels) |

Опционально (режим `cloud`): `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` — для
извлечения ключевых слов через LLM (любой OpenAI-совместимый API).
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
| `AI_MODE` | `local` | `local` — всё локально (Ollama+TTS+Whisper), `cloud` — облачные сервисы |
| `LOCAL_LLM_PROVIDER` | `ollama` | Локальный LLM (только `ollama` в local-режиме) |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | URL локального Ollama (только localhost) |
| `OLLAMA_MODEL` | `qwen3:8b` | Модель Ollama (не зашита в код) |
| `LOCAL_TTS_ENGINE` | `espeak-ng` | Локальный TTS: `espeak-ng` / `piper` |
| `LOCAL_ASR_ENGINE` | `whisper` | Локальный ASR (word-level тайминг) |
| `YANDEX_API_KEY` / `YANDEX_IAM_TOKEN` | — | Ключ SpeechKit (только `cloud`) |
| `YANDEX_FOLDER_ID` | — | Каталог Yandex Cloud (только `cloud`) |
| `PEXELS_API_KEY` | — | Ключ Pexels (сток, `cloud` или `auto` без локальной библиотеки) |
| `PIXABAY_API_KEY` | — | Ключ Pixabay (fallback для Pexels) |
| `VIDEO_SOURCE` | `auto` | `local` / `online` / `auto` (local → online → local) |
| `LOCAL_MEDIA_DIR` | `data/media` | Директория локальной видеотеки |
| `MIN_CLIPS_PER_PHRASE` | `2` | Минимум клипов на фразу из Pexels, иначе Pixabay fallback |
| `MIN_CLIP_WIDTH` | `720` | Мин. ширина клипа (фильтр «мыла») |
| `CACHE_ENABLED` | `true` | Sha256-кэш скачанных клипов (dedup); `false` — кэш полностью выключен |
| `CACHE_DIR` | `cache` | Директория кэша клипов |
| `CACHE_TTL_DAYS` | `7` | TTL кэша |
| `MAX_CACHE_SIZE_MB` | `500` | Лимит размера кэша (LRU) |
| `RENDER_CONCURRENCY` | `1` | Параллельный рендер (RTX 3050/16GB — 1) |
| `JOB_CONCURRENCY` | `1` | Параллельные задания (RTX 3050/16GB — 1) |
| `VIDEO_PADDING` | `blur` | `blur` — размытая подложка, `crop` — кроп |
| `LLM_PROVIDER` | `local` | `local` (Ollama) или `openai` (облако) |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | LLM для облачного режима (YandexGPT, OpenAI…) |
| `LLM_TIMEOUT` / `LLM_RETRIES` | `60` / `2` | Таймаут и ретраи LLM |
| `SCRIPT_GENERATION` | `off` | `on`/`off`/`auto` — генерация сценария (хук/тело/концовка) |
| `SCENES_MAX` | `12` | Максимум сцен в ролике |
| `SUBTITLE_STYLE` | `tiktok` | `classic`/`tiktok`/`news`/`gaming`/`minimal` |
| `DATA_DIR` | `data` | Директория БД |
| `DB_PATH` | `data/video_bot.db` | Файл SQLite (пользователи, история, новостные выпуски) |
| `IMAGE_FALLBACK` | `true` | Fallback-фон (анимированный градиент), если видео не найдено |
| `BACKGROUND_MUSIC` | `false` | Вкл/выкл фоновую музыку |
| `BG_MUSIC_PATH` | `music/ambience.mp3` | Путь к музыкальному файлу |
| `BG_MUSIC_VOLUME` | `-23` | Громкость музыки в dB (приглушение sidechain) |
| `TTS_VOICE` / `TTS_EMOTION` / `TTS_SPEED` / `TTS_LANG` | — | Параметры голоса (режим `cloud`) |
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
| `MAX_NEWS_PER_BATCH` | `20` | Макс. новостей в выпуске |
| `MAX_NEWS_TEXT_LENGTH` | `10000` | Макс. символов в одной новости |
| `MAX_TOTAL_BATCH_LENGTH` | `100000` | Макс. символов во всех новостях выпуска |
| `INTRO_ENABLED` / `OUTRO_ENABLED` | `true` | Вступление/завершение выпуска |
| `TRANSITIONS_ENABLED` | `true` | Переходы между новостями |
| `TRANSITION_TYPE` | `crossfade` | `crossfade` (xfade+acrossfade) или `fade` |
| `TRANSITION_DURATION` | `0.5` | Длительность перехода (сек) |
| `NEWS_TITLE_ENABLED` | `true` | Title card (заголовок новости перед озвучкой) |
| `NEWS_TITLE_DURATION` | `1.0` | Длительность title card (сек) |

Послушать голоса: [страница SpeechKit](https://yandex.cloud/ru/services/speechkit),
вкладка «Синтез речи» в консоли, или [AI Studio](https://aistudio.yandex.ru/ai-speech).

## Структура проекта

```
bot.py               # точка входа, обработчики Telegram, inline-клавиатуры, настройки, отмена, выпуск новостей
pipeline.py          # оркестратор пайплайна (одиночная новость), разбивка длинных текстов
pipeline_news.py     # пайплайн выпуска новостей (несколько новостей -> ОДИН MP4)
config.py            # чтение .env, константы (в т.ч. AI_MODE/OLLAMA/новостные настройки)
tts.py               # синтез речи (SpeechKit v1, cloud), word-level ASR (v3), склейка с кроссфейдом
tts_local.py         # локальный TTS (espeak-ng/piper) без облака
video_source.py      # VideoSourceProvider: PexelsProvider + PixabayProvider + SteamProvider
video_render.py      # сборка финального видео через ffmpeg, probe_video, validate_output
video_render_unified.py # единый рендер UnifiedTimeline с crossfade-переходами и субтитрами
prompts.py           # промпты для LLM (вынесены из video_source.py)
cache.py             # дисковый кэш клипов (TTL, LRU)

ai/                  # провайдер LLM: base.py, openai_compat.py (cloud), ollama.py (local), factory.py
news/                # News Batch: models.py, parser.py, editor.py, dedup.py, ordering.py,
                     # transitions.py, script.py, timeline.py, asr.py
script/              # analyzer.py (анализ текста), generator.py (сценарий), scene_planner.py (сцены)
video/               # ranking.py, selector.py, downloader.py, cache.py (sha256), fallback.py, local.py
subtitles/           # generator.py (.ass/.srt), alignment.py (тайминг), styles.py (пресеты)
storage/             # database.py (SQLite: users/jobs/videos/news_batches/news_items/timeline_items),
                     # history.py (история генераций)
utils/               # retry.py (ретраи с backoff), hashing.py (sha256), cleanup.py (очистка),
                     # cancellation.py (токены отмены), json_utils.py (безопасный JSON), errors.py

tests/               # тесты (pytest)
data/                # SQLite-БД, локальная видеотека media/ (создаётся автоматически)
requirements.txt     # основные зависимости
requirements-optional.txt # опциональные (whisper-timestamped из GitHub)
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

Плюс inline-клавиатура: **🎬 Создать видео**, **📰 Создать выпуск из новостей**,
**📚 История**, **⚙️ Настройки** (голос, скорость, источник видео, стиль субтитров,
формат — сохраняются в SQLite), **ℹ️ Помощь**. Во время генерации — единый
статус-трейсер с кнопкой **❌ Отменить**.

### Выпуск из новостей (режим `local`)

1. Нажми **📰 Создать выпуск из новостей**.
2. Присылай новости по одной (текстом) — бот показывает счётчик.
3. Нажми **✅ Готово** — появится подтверждение:
   **«Получено N новостей»** и кнопки **✅ Создать видео / ✏️ Изменить / ❌ Отмена**.
4. **✅ Создать видео** — локальная LLM отредактирует новости, уберёт повторы,
   отсортирует по важности, добавит вступление и переходы, озвучит локальным TTS
   и соберёт в **один MP4** с переходами и субтитрами.
5. **✏️ Изменить** — добавить новую новость или отправить «убрать» для удаления
   последней. **❌ Отмена** — сбросить сбор.

## Разработка

```bash
# Установка dev-зависимостей
pip install -e ".[dev]"   # или: pip install -r requirements-dev.txt

# Запуск тестов
pytest tests/

# Линтер
ruff check .
```

## Диагностика

```bash
# Проверить конфигурацию локального режима при запуске
python bot.py    # при AI_MODE=local проверит Ollama и модель

# Проверить Ollama напрямую
curl http://127.0.0.1:11434/api/tags
nvidia-smi        # GPU должна быть видна Ollama (показывает процессы)
```

Если бот не запускается в `local`-режиме — проверьте:
- запущен ли Ollama (`ollama list` / `curl http://127.0.0.1:11434/api/tags`);
- скачана ли модель из `OLLAMA_MODEL` (`ollama pull qwen3:8b`);
- установлен ли локальный TTS (`which espeak-ng`).