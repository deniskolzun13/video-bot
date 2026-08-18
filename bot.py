import asyncio
import io
import logging
import time
from pathlib import Path

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message

import config
from pipeline import process_text
from tts import TTSError
from video_source import VideoSourceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
semaphore = asyncio.Semaphore(config.BOT_CONCURRENCY)


class AccessMiddleware(BaseMiddleware):
    """Бот работает только для одного пользователя (ALLOWED_USER_ID)."""

    async def __call__(self, handler, event: Message, data: dict):
        user = event.from_user
        if user is None:
            return
        if str(user.id) != config.ALLOWED_USER_ID:
            logger.warning(
                "Доступ запрещён: user_id=%s username=%s (ALLOWED_USER_ID=%s)",
                user.id, user.username, config.ALLOWED_USER_ID,
            )
            await event.answer("⛔ Этот бот доступен только владельцу.")
            return
        return await handler(event, data)


dp.message.middleware(AccessMiddleware())

HELP_TEXT = (
    "Пришли текст новости сообщением (или .txt файлом) — "
    "сделаю вертикальное видео 9:16 с озвучкой и субтитрами.\n\n"
    "Ограничения:\n"
    f"• до {config.MAX_VIDEO_SYMBOLS} символов — один ролик\n"
    f"• до {config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS} символов — разобью на "
    f"{config.MAX_PARTS} ролика\n"
    f"• длительность озвучки не больше {config.MAX_VIDEO_DURATION:.0f} секунд"
)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(HELP_TEXT)


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@dp.message(Command("clear_cache"))
async def cmd_clear_cache(message: Message) -> None:
    from cache import clear_cache
    count = clear_cache()
    await message.answer(f"🗑 Кэш очищен. Удалено файлов: {count}")


@dp.message(F.text & ~F.command)
async def on_text(message: Message) -> None:
    await _handle(message, message.text)


@dp.message(F.document)
async def on_document(message: Message) -> None:
    document = message.document
    if not (document.file_name or "").lower().endswith(".txt"):
        await message.answer("Поддерживаются только .txt файлы. Или просто пришли текст сообщением.")
        return
    buf = io.BytesIO()
    await bot.download(document, destination=buf)
    text = buf.getvalue().decode("utf-8", errors="replace")
    await _handle(message, text)


async def _handle(message: Message, text: str) -> None:
    text = text.strip()
    if not text:
        await message.answer("Текст пустой.")
        return
    if len(text) > config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS:
        await message.answer(
            f"Слишком длинный текст: {len(text)} символов (лимит "
            f"{config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS}). Сократи его."
        )
        return

    async with semaphore:
        await message.answer("✅ Принял. Запускаю пайплайн, это может занять 1–3 минуты…")

        async def notify(status: str) -> None:
            logger.info("[user %s] %s", message.from_user.id, status)
            await message.answer(status)

        try:
            videos = await process_text(
                text,
                work_dir=Path(config.WORK_DIR) / f"{message.from_user.id}_{int(time.time())}",
                notify=notify,
            )
            for i, video in enumerate(videos, 1):
                caption = "🎬 Готово! Видео к публикации." if len(videos) == 1 else f"🎬 Часть {i}/{len(videos)}"
                await message.answer_video(FSInputFile(video), caption=caption)
        except TTSError as exc:
            logger.error("TTS ошибка для пользователя %s: %s", message.from_user.id, exc.details or exc)
            await message.answer(f"🔊 Ошибка озвучки: {exc}\n\nПопробуйте сократить текст или повторите позже.")
        except VideoSourceError as exc:
            logger.error("Ошибка видео-источника для пользователя %s: %s", message.from_user.id, exc.details or exc)
            await message.answer(f"🎬 Ошибка подбора видео: {exc}\n\nПопробуйте другую новость или повторите позже.")
        except Exception as exc:
            logger.exception("Пайплайн упал для пользователя %s", message.from_user.id)
            await message.answer("❌ Внутренняя ошибка. Попробуйте ещё раз. Если повторится — проверьте логи.")


async def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")
    Path(config.WORK_DIR).mkdir(exist_ok=True)
    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())