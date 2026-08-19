"""Очистка временных файлов. Кэш и финальный output не трогаем."""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Паттерны временных файлов, которые можно удалять
TEMP_PATTERNS = ("*.pcm", "*.pcm16k", "*.wav", "*.part", "*.tmp")


def safe_unlink(path: Path) -> None:
    """Безопасное удаление файла без исключений."""
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Не удалось удалить %s: %s", path, exc)


def cleanup_dir(directory: Path, keep: tuple[str, ...] = ()) -> int:
    """Удаляет временные файлы в директории. Возвращает число удалённых.

    keep — имена файлов/каталогов, которые НЕ трогаем (например финальный mp4).
    """
    directory = Path(directory)
    if not directory.exists():
        return 0
    count = 0
    for pattern in TEMP_PATTERNS:
        for f in directory.glob(pattern):
            if f.name in keep:
                continue
            try:
                f.unlink()
                count += 1
            except Exception as exc:
                logger.debug("Не удалось удалить %s: %s", f, exc)
    return count


def remove_tree(directory: Path) -> None:
    """Полное удаление временной рабочей директории задачи."""
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception as exc:
        logger.debug("Не удалось удалить каталог %s: %s", directory, exc)