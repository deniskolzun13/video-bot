"""SQLite-хранилище: users, jobs, videos."""
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

_DB_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    voice TEXT DEFAULT 'zahar',
    speed REAL DEFAULT 1.0,
    video_source TEXT DEFAULT 'auto',
    subtitle_style TEXT DEFAULT 'tiktok',
    format TEXT DEFAULT 'mp4',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    status TEXT DEFAULT 'pending',
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
                         fields.get("format", "mp4")),
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
            "format": user["format"] if user else "mp4",
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
                    "VALUES (?, ?, 'pending', ?, ?, ?)",
                    (job_id, user_id, source_text, script, _now()),
                )

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
                "WHERE j.user_id = ? AND j.status = 'done' "
                "ORDER BY j.created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]


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