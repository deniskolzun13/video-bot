"""Скачивание клипов с кэшем и повторными попытками."""
import logging
from pathlib import Path

from utils.retry import RetryableError, retry_async

logger = logging.getLogger(__name__)


async def download_clip(provider, clip, dest: Path, retries: int = 3) -> Path:
    """Скачивает клип через провайдера (у которого уже есть кэш) с ретраями."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    async def _do() -> Path:
        return await provider.download(clip, dest)

    try:
        return await retry_async(_do, retries=retries, base_delay=1.0)
    except RetryableError as exc:
        logger.warning("Скачивание клипа не удалось после ретраев: %s", exc)
        raise ValueError(f"Не удалось скачать видео: {exc}") from exc