"""История генераций через SQLite."""
import logging

from storage.database import Database, get_db

logger = logging.getLogger(__name__)


def save_job_history(
    db: Database | None,
    job_id: str,
    user_id: int,
    source_text: str,
    script: str = "",
    status: str = "done",
    output_path: str = "",
    duration: float = 0,
    error: str = "",
) -> None:
    """Сохраняет результат генерации в историю (БД уже создана вызывающим)."""
    if db is None:
        return
    try:
        if not db.get_job(job_id):
            db.create_job(job_id, user_id, source_text, script)
        if status == "done":
            db.finish_job(job_id, status, output_path, error)
            if output_path:
                db.add_video(job_id, output_path, duration)
        else:
            db.finish_job(job_id, status, output_path, error)
    except Exception as exc:
        logger.warning("Не удалось сохранить историю: %s", exc)


def list_history(user_id: int, limit: int = 10) -> list[dict]:
    try:
        return get_db().list_history(user_id, limit)
    except Exception as exc:
        logger.warning("Не удалось получить историю: %s", exc)
        return []