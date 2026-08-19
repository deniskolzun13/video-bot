"""Хранилище: SQLite БД + история генераций."""
from storage.database import Database, get_db
from storage.history import list_history, save_job_history

__all__ = ["Database", "get_db", "list_history", "save_job_history"]