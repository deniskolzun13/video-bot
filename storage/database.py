"""SQLite-хранилище: users, jobs, videos."""
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

_DB_LOCK = threading.Lock()

# Допустимые статусы job (см. ТЗ v2.0.1: cancellation / job states
# и раздел 40: queued, collecting_news, analyzing, editing, deduplicating,
# ordering, planning, tts, alignment, video_search, timeline, rendering,
# validating, completed, failed, cancelled).
JOB_STATUSES = (
    "queued", "collecting_news", "analyzing", "editing", "deduplicating",
    "ordering", "planning", "tts", "alignment", "video_search", "timeline",
    "scripting", "searching", "downloading", "subtitles", "rendering",
    "validating", "completed", "failed", "cancelled",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    voice TEXT DEFAULT 'zahar',
    speed REAL DEFAULT 1.0,
    video_source TEXT DEFAULT 'auto',
    subtitle_style TEXT DEFAULT 'tiktok',
    format TEXT DEFAULT 'vertical',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    status TEXT DEFAULT 'queued',
    source_text TEXT,
    script TEXT,
    output_path TEXT,
    created_at REAL,
    completed_at REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    path TEXT,
    duration REAL DEFAULT 0,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS news_batches (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    news_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'queued',
    output_path TEXT,
    created_at REAL,
    completed_at REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    news_id INTEGER,
    original_text TEXT,
    edited_text TEXT,
    title TEXT,
    summary TEXT,
    keywords TEXT,
    importance REAL DEFAULT 0.5,
    position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS timeline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    item_type TEXT,
    news_id INTEGER,
    start REAL DEFAULT 0,
    end REAL DEFAULT 0,
    duration REAL DEFAULT 0,
    text TEXT,
    audio_path TEXT,
    phrase_timings TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_videos_job_id ON videos(job_id);
CREATE INDEX IF NOT EXISTS idx_news_batches_user ON news_batches(user_id);
CREATE INDEX IF NOT EXISTS idx_news_items_batch ON news_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_timeline_batch ON timeline_items(batch_id);
"""


def _now() -> float:
    return time.time()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Тонкая обёртка над SQLite с потокобезопасными операциями."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or getattr(config, "DB_PATH", "data/video_bot.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Лёгкие миграции: ALTER TABLE для колонок, добавленных в новых версиях."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(timeline_items)")}
        if "phrase_timings" not in cols:
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE timeline_items ADD COLUMN phrase_timings TEXT"
                )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # --- users ---
    def upsert_user(self, user_id: int, username: str = "", **fields: Any) -> None:
        with _DB_LOCK:
            existing = self._conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            with self._conn:
                if existing is None:
                    self._conn.execute(
                        "INSERT INTO users (user_id, username, created_at, voice, speed, video_source, subtitle_style, format) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, username, _now(),
                         fields.get("voice", "zahar"), fields.get("speed", 1.0),
                         fields.get("video_source", "auto"),
                         fields.get("subtitle_style", "tiktok"),
                         fields.get("format", "vertical")),
                    )
                else:
                    cols = {k: v for k, v in fields.items() if k in (
                        "voice", "speed", "video_source", "subtitle_style", "format")}
                    if cols:
                        sets = ", ".join(f"{k} = ?" for k in cols)
                        self._conn.execute(
                            f"UPDATE users SET {sets}, username = ? WHERE user_id = ?",
                            (*cols.values(), username or existing["username"], user_id),
                        )

    def get_user(self, user_id: int) -> dict | None:
        with _DB_LOCK:
            row = self._conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_user_settings(self, user_id: int) -> dict:
        user = self.get_user(user_id)
        return {
            "voice": user["voice"] if user else config.TTS_VOICE,
            "speed": float(user["speed"]) if user else float(config.TTS_SPEED),
            "video_source": user["video_source"] if user else config.VIDEO_SOURCE,
            "subtitle_style": user["subtitle_style"] if user else config.SUBTITLE_STYLE,
            "format": user["format"] if user else "vertical",
        }

    # --- jobs ---
    def next_job_id(self) -> str:
        """JOB-YYYYMMDD-NNNN (сквозной счётчик, уникальный в пределах дня)."""
        with _DB_LOCK:
            date = datetime.now().strftime("%Y%m%d")
            row = self._conn.execute(
                "SELECT id FROM jobs WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
                (f"JOB-{date}-%",),
            ).fetchone()
            if row is None:
                return f"JOB-{date}-0001"
            last = row["id"]
            try:
                num = int(last.rsplit("-", 1)[1]) + 1
            except (ValueError, IndexError):
                num = 1
            return f"JOB-{date}-{num:04d}"

    def create_job(self, job_id: str, user_id: int, source_text: str, script: str = "") -> None:
        with _DB_LOCK:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO jobs (id, user_id, status, source_text, script, created_at) "
                    "VALUES (?, ?, 'queued', ?, ?, ?)",
                    (job_id, user_id, source_text, script, _now()),
                )

    def set_stage(self, job_id: str, status: str) -> None:
        """Обновляет статус job. Игнорирует неизвестные статусы."""
        if status not in JOB_STATUSES:
            return
        self.update_job(job_id, status=status)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        with _DB_LOCK:
            sets = ", ".join(f"{k} = ?" for k in fields)
            with self._conn:
                self._conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id))

    def get_job(self, job_id: str) -> dict | None:
        with _DB_LOCK:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_jobs(self, user_id: int, limit: int = 20) -> list[dict]:
        with _DB_LOCK:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def finish_job(self, job_id: str, status: str, output_path: str = "", error: str = "") -> None:
        self.update_job(job_id, status=status, output_path=output_path,
                        error=error, completed_at=_now())

    # --- videos ---
    def add_video(self, job_id: str, path: str, duration: float = 0) -> None:
        with _DB_LOCK:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO videos (job_id, path, duration, created_at) VALUES (?, ?, ?, ?)",
                    (job_id, path, duration, _now()),
                )

    def list_history(self, user_id: int, limit: int = 10) -> list[dict]:
        """Последние завершённые видео пользователя с job-инфо."""
        with _DB_LOCK:
            rows = self._conn.execute(
                "SELECT j.id AS job_id, j.status, j.created_at, v.path, v.duration "
                "FROM jobs j LEFT JOIN videos v ON v.job_id = j.id "
                "WHERE j.user_id = ? AND j.status = 'completed' "
                "ORDER BY j.created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- news_batches / news_items / timeline_items ---
    def create_news_batch(self, batch_id: str, user_id: int, news_count: int) -> None:
        with _DB_LOCK:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO news_batches (id, user_id, news_count, status, created_at) "
                    "VALUES (?, ?, ?, 'queued', ?)",
                    (batch_id, user_id, news_count, _now()),
                )

    def update_news_batch(self, batch_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"status", "output_path", "error", "completed_at", "news_count"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        with _DB_LOCK:
            sets = ", ".join(f"{k} = ?" for k in fields)
            with self._conn:
                self._conn.execute(
                    f"UPDATE news_batches SET {sets} WHERE id = ?",
                    (*fields.values(), batch_id),
                )

    def get_news_batch(self, batch_id: str) -> dict | None:
        with _DB_LOCK:
            row = self._conn.execute(
                "SELECT * FROM news_batches WHERE id = ?", (batch_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_news_item(self, batch_id: str, item) -> None:
        """Сохраняет NewsItem. keywords — JSON-строка."""
        with _DB_LOCK:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO news_items (batch_id, news_id, original_text, edited_text, "
                    "title, summary, keywords, importance, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        batch_id, item.id, item.original_text, item.edited_text,
                        item.title, item.summary, json.dumps(item.keywords, ensure_ascii=False),
                        item.importance, item.id,
                    ),
                )

    def save_timeline_item(self, batch_id: str, item) -> None:
        with _DB_LOCK:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO timeline_items (batch_id, item_type, news_id, start, end, "
                    "duration, text, audio_path, phrase_timings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        batch_id, item.type, item.news_id, item.start, item.end,
                        item.duration, item.text, item.audio_path,
                        json.dumps(item.phrase_timings, ensure_ascii=False)
                        if getattr(item, "phrase_timings", None)
                        else None,
                    ),
                )

    def list_news_items(self, batch_id: str) -> list[dict]:
        with _DB_LOCK:
            rows = self._conn.execute(
                "SELECT * FROM news_items WHERE batch_id = ? ORDER BY position",
                (batch_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_timeline_items(self, batch_id: str) -> list[dict]:
        with _DB_LOCK:
            rows = self._conn.execute(
                "SELECT * FROM timeline_items WHERE batch_id = ? ORDER BY start",
                (batch_id,),
            ).fetchall()
            items = [dict(r) for r in rows]
            for item in items:
                raw = item.get("phrase_timings")
                item["phrase_timings"] = json.loads(raw) if raw else None
            return items


# Единый инстанс на процесс (глобальное состояние допустимо для БД).
_db: Database | None = None
_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _lock:
            if _db is None:
                _db = Database()
    return _db