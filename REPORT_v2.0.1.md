# VIDEO-BOT v2.0.1 HARDENING REPORT

Дата: 19.08.2026
Ветка: `master` (`https://github.com/deniskolzun13/video-bot`)
Деплой: `/home/koldun/мее` (systemd-user `gamenews-bot.service`, PID 482744, бот `@koldunrerebot`)

## Статус

- PHASE 1–8 — выполнены полностью.
- 163 теста (pytest) — зелёные. `ruff check` — чисто. `compileall` — без ошибок.
- Изменения опубликованы в 3 коммитах: `3265e4d`, `7015441`, `c0b2723`.

## Что сделано

### PHASE 1 — Кэш и планировщик сцен
- `cache.py` — атомарная запись (mkstemp → fsync → os.replace), блокировка, исправлен баг двойного деления `max_size_mb`.
- `video/cache.py` — единый интерфейс кэша (`cache_enabled/cache_get/cache_put/cache_stats/cache_key_for_clip/...`), уважает `CACHE_ENABLED=false`.
- `video_source.py` — ленивые импорты `video.cache` внутри `download` (снимает циклический импорт).
- `script/scene_planner.py` — сцены без поля `text`; привязка фраз через `phrase_indexes` + `map_scenes_to_phrases`; валидация `ScenePlan.validate()`.

### PHASE 2 — Forced alignment
- `subtitles/alignment.py` — `normalize_word`, нечёткое сопоставление слов, whisper-синглтон с таймаутом `ALIGNMENT_TIMEOUT_SECONDS`; цепочка Yandex ASR → whisper → пропорциональный.

### PHASE 3 — Отмена задач
- `utils/cancellation.py` — `CancellationToken`/`CancellationError`; проверки на каждом этапе пайплайна.
- `video_render.py` — Popen + `communicate(timeout=RENDER_TIMEOUT_SECONDS)` + terminate/kill.
- SQLite — статусы `completed`/`failed`/`cancelled`, `set_stage`, индексы, история фильтрует completed.

### PHASE 4 — Прозрачный подбор видео
- `video/ranking.py` — веса (релевантность 0.40, ориентация 0.20, разрешение 0.15, длительность 0.10, keywords 0.15), дубликаты → -1000.
- `video/selector.py` — fallback Pexels → Pixabay, повтор лучшего клипа, fallback-фон.

### PHASE 5 — Надёжный LLM
- Ретраи 408/429/5xx, авторизация auto/bearer/api-key, робастный JSON-парсер (`utils/json_utils.py`) без eval/exec.

### PHASE 6 — Concurrency, формат, job dirs, логирование
- `JOB_CONCURRENCY` и `RENDER_CONCURRENCY` раздельно; `job_semaphore`/`render_semaphore` в боте.
- Формат видео (vertical/square/landscape) в настройках бота; `resolve_video_size`; рендер и валидация по целевому разрешению.
- Артефакты заданий в `data/jobs/<JOB_ID>/{input,tts,video,subtitles,output}`.
- `validate_config` при старте; иерархия ошибок в `utils/errors.py`; логирование с `job_id` (contextvar).
- История показывает «Файл больше недоступен»; исправлено 401-сообщение TTS (Yandex API-ключ).

### PHASE 7 — Интеграционные тесты и документация
- `tests/test_pipeline_integration.py` — полный прогон `process_text` с фейками (FakeLLM/TTS/ASR/VideoProvider/Renderer).
- README (раздел v2.0.1) и `.env.example` обновлены; pytest-asyncio в `requirements-dev.txt`.

### PHASE 8 — Деплой
- Код синхронизирован в `/home/koldun/мее` (rsync с исключением `.env`/данных), сервис перезапущен, логи чистые, бот отвечает на `getMe`.

## Замечания

- `IMAGE_FALLBACK`/`IMAGE_KEN_BURNS` — настроечный резерв (Ken Burns не используется в текущем пайплайне; fallback-фон — анимированный градиент).
- Отчётные артефакты: `.env` и данные не коммитятся (в `.gitignore`).