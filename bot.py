import asyncio
import io
import logging
import time
import uuid
from pathlib import Path

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from pipeline import process_text
from storage import get_db, list_history
from tts import TTSError
from utils.cancellation import CancellationError, CancellationToken
from utils.cleanup import remove_tree
from utils.errors import ConfigurationError, InternalError, ProviderError, UserError, ValidationError
from video_source import VideoSourceError
from utils.logging import job_context, setup_logging

setup_logging(logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
# Ограничение параллельных генераций (весь job) и параллельных ffmpeg-рендеров
job_semaphore = asyncio.Semaphore(config.JOB_CONCURRENCY)
render_semaphore = asyncio.Semaphore(config.RENDER_CONCURRENCY)

# Словарь: user_id -> CancellationToken для отмены задачи
# Активные токены отмены по job_id (не по user_id): отмена не заденет
# другие генерации пользователя.
_cancel_tokens: dict[str, CancellationToken] = {}

# Маппинг этапа (префикс эмодзи в notify) -> статус job в БД
STAGE_TO_JOB_STATUS = {
    "🧠": "analyzing",
    "✍️": "scripting",
    "🎙": "tts",
    "⏱": "alignment",
    "🎞": "searching",
    "📝": "subtitles",
    "⚙️": "rendering",
    "✅": "validating",
}


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


class CallbackAccessMiddleware(BaseMiddleware):
    """Контроль доступа для callback-запросов (кнопок)."""

    async def __call__(self, handler, event: CallbackQuery, data: dict):
        user = event.from_user
        if user is None:
            return
        if str(user.id) != config.ALLOWED_USER_ID:
            await event.answer("⛔ Доступ запрещён.", show_alert=True)
            return
        return await handler(event, data)


dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(CallbackAccessMiddleware())

HELP_TEXT = (
    "🎬 <b>Генератор вертикальных видео</b>\n\n"
    "Пришли текст новости сообщением (или .txt файлом) — сделаю вертикальное видео 9:16 "
    "с озвучкой, субтитрами и видео-подложкой.\n\n"
    "Ограничения:\n"
    f"• до {config.MAX_VIDEO_SYMBOLS} символов — один ролик\n"
    f"• до {config.MAX_VIDEO_SYMBOLS * config.MAX_PARTS} символов — разобью на "
    f"{config.MAX_PARTS} ролика\n"
    f"• длительность озвучки не больше {config.MAX_VIDEO_DURATION:.0f} секунд\n\n"
    "Используй кнопки ниже 👇"
)


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Создать видео", callback_data="menu")],
        [InlineKeyboardButton(text="📚 История", callback_data="history"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")],
    ])


def settings_kb(user_id: int) -> InlineKeyboardMarkup:
    s = get_db().get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎙 Голос: {s['voice']}", callback_data="set_voice")],
        [InlineKeyboardButton(text=f"⚡ Скорость: {s['speed']}", callback_data="set_speed")],
        [InlineKeyboardButton(text=f"🎞 Источник видео: {s['video_source']}", callback_data="set_video_source")],
        [InlineKeyboardButton(text=f"💬 Стиль субтитров: {s['subtitle_style']}", callback_data="set_subtitle_style")],
        [InlineKeyboardButton(text=f"📐 Формат: {s['format']}", callback_data="set_format")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")],
    ])


def voice_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎙 Zahar (мужской, энергичный)", callback_data="val_voice:zahar")],
        [InlineKeyboardButton(text="🎙 Ermil (мужской)", callback_data="val_voice:ermil")],
        [InlineKeyboardButton(text="🎙 Jane (женский)", callback_data="val_voice:jane")],
        [InlineKeyboardButton(text="🎙 Omazh (женский)", callback_data="val_voice:omazh")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


def speed_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🐢 0.8", callback_data="val_speed:0.8")],
        [InlineKeyboardButton(text="⚡ 1.0", callback_data="val_speed:1.0")],
        [InlineKeyboardButton(text="🚀 1.2", callback_data="val_speed:1.2")],
        [InlineKeyboardButton(text="🚀 1.5", callback_data="val_speed:1.5")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


def video_source_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Авто (Steam → Pexels)", callback_data="val_video_source:auto")],
        [InlineKeyboardButton(text="🎮 Только Steam", callback_data="val_video_source:steam")],
        [InlineKeyboardButton(text="🎬 Только Pexels", callback_data="val_video_source:pexels")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


def subtitle_style_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ TikTok", callback_data="val_subtitle_style:tiktok")],
        [InlineKeyboardButton(text="📰 News", callback_data="val_subtitle_style:news")],
        [InlineKeyboardButton(text="🎮 Gaming", callback_data="val_subtitle_style:gaming")],
        [InlineKeyboardButton(text="🎬 Classic", callback_data="val_subtitle_style:classic")],
        [InlineKeyboardButton(text="➖ Minimal", callback_data="val_subtitle_style:minimal")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


def format_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Вертикальный 9:16 (1080×1920)", callback_data="val_format:vertical")],
        [InlineKeyboardButton(text="⬛ Квадрат 1:1 (1080×1080)", callback_data="val_format:square")],
        [InlineKeyboardButton(text="🖥 Горизонтальный 16:9 (1920×1080)", callback_data="val_format:landscape")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="settings")],
    ])


def after_video_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перегенерировать", callback_data="regen")],
        [InlineKeyboardButton(text="📝 Новый текст", callback_data="menu")],
    ])


def cancel_kb(job_id: str | None = None) -> InlineKeyboardMarkup:
    data = f"cancel:{job_id}" if job_id else "cancel"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить", callback_data=data)],
    ])


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db = get_db()
    db.upsert_user(message.from_user.id, message.from_user.username or "")
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())


@dp.message(Command("clear_cache"))
async def cmd_clear_cache(message: Message) -> None:
    from cache import clear_cache
    count = clear_cache()
    await message.answer(f"🗑 Кэш очищен. Удалено файлов: {count}")


@dp.message(F.text & ~F.command)
async def on_text(message: Message) -> None:
    if message.text in ("🎬 Создать видео", "📰 Создать из новости", "📚 История",
                        "⚙️ Настройки", "ℹ️ Помощь"):
        return
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


# --- Callbacks ---
@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(HELP_TEXT, reply_markup=main_menu_kb())


@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(HELP_TEXT, reply_markup=main_menu_kb())


@dp.callback_query(F.data == "history")
async def cb_history(call: CallbackQuery) -> None:
    await call.answer()
    user_id = call.from_user.id
    history = list_history(user_id, limit=10)
    if not history:
        await call.message.edit_text("📚 История пуста.\n\nСоздай первое видео!",
                                     reply_markup=main_menu_kb())
        return
    lines = ["📚 <b>История генераций</b>\n"]
    for i, item in enumerate(history[:10], 1):
        job = item.get("job_id", "—")
        created = item.get("created_at") or 0
        ts = time.strftime("%d.%m %H:%M", time.localtime(created))
        dur = item.get("duration") or 0
        status = "✅" if item.get("status") == "completed" else "❌"
        line = f"{i}. {status} {job} · {ts} · {dur:.0f}с"
        video_path = item.get("path")
        if video_path:
            if not Path(video_path).exists():
                line += "\n   ⚠️ Файл больше недоступен"
        lines.append(line)
    await call.message.edit_text("\n".join(lines), reply_markup=main_menu_kb())


@dp.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text("⚙️ <b>Настройки</b>\n\nВыбери параметр для изменения:",
                                 reply_markup=settings_kb(call.from_user.id))


@dp.callback_query(F.data.startswith("set_"))
async def cb_set(call: CallbackQuery) -> None:
    await call.answer()
    action = call.data[4:]
    kb = {
        "voice": voice_kb,
        "speed": speed_kb,
        "video_source": video_source_kb,
        "subtitle_style": subtitle_style_kb,
        "format": format_kb,
    }[action]()
    await call.message.edit_text("Выбери значение:", reply_markup=kb)


@dp.callback_query(F.data.startswith("val_"))
async def cb_val(call: CallbackQuery) -> None:
    await call.answer()
    param, value = call.data[4:].split(":", 1)
    db = get_db()
    user_id = call.from_user.id
    db.upsert_user(user_id, call.from_user.username or "", **{param: value})
    await call.message.edit_text(
        f"✅ Настройка <b>{param}</b> обновлена: <b>{value}</b>",
        reply_markup=settings_kb(user_id),
    )


@dp.callback_query(F.data.startswith("cancel"))
async def cb_cancel(call: CallbackQuery) -> None:
    await call.answer("Отменяю генерацию…")
    job_id = call.data.split(":", 1)[1] if ":" in call.data else None
    if job_id:
        token = _cancel_tokens.get(job_id)
        if token:
            token.cancel()
    else:
        # Старая кнопка без job_id: отменяем любую активную генерацию пользователя
        for token in _cancel_tokens.values():
            token.cancel()
    try:
        await call.message.edit_text("❌ Генерация отменена.", reply_markup=main_menu_kb())
    except Exception:
        pass


@dp.callback_query(F.data == "regen")
async def cb_regen(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_text(
        "Пришли новый текст сообщением или .txt файлом, или нажми «🎬 Создать видео».",
        reply_markup=main_menu_kb(),
    )


# --- Основная обработка ---
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

    db = get_db()
    db.upsert_user(message.from_user.id, message.from_user.username or "")
    job_id = db.next_job_id()

    async with job_semaphore:
        status_msg = await message.answer("🔄 Ставлю задачу в очередь…", reply_markup=cancel_kb(job_id))
        token = CancellationToken()
        _cancel_tokens[job_id] = token

        async def notify(status: str) -> None:
            logger.info("[%s] [user %s] %s", job_id, message.from_user.id, status)
            token.check()
            for prefix, db_status in STAGE_TO_JOB_STATUS.items():
                if status.startswith(prefix):
                    try:
                        db.update_job(job_id, status=db_status)
                    except Exception:
                        pass
                    break
            try:
                await status_msg.edit_text(status, reply_markup=cancel_kb(job_id))
            except Exception:
                pass

        task_id = uuid.uuid4().hex[:8]
        # Структура артефактов job: data/jobs/<JOB_ID>/
        #   input/   — исходные файлы
        #   tts/     — озвучка
        #   video/   — скачанные клипы
        #   subtitles/ — subs.ass / subs.srt
        #   output/  — готовые mp4
        job_root = Path(config.JOB_DIR) / job_id
        for sub in ("input", "tts", "video", "subtitles", "output"):
            (job_root / sub).mkdir(parents=True, exist_ok=True)
        work_dir = job_root / "input"
        db.create_job(job_id, message.from_user.id, text)

        try:
            with job_context(job_id):
                videos = await process_text(
                    text,
                    work_dir=work_dir,
                    notify=notify,
                    task_id=task_id,
                    job_id=job_id,
                    user_id=message.from_user.id,
                    settings=db.get_user_settings(message.from_user.id),
                    cancel_token=token,
                    render_semaphore=render_semaphore,
                    job_dir=job_root,
                )
            for i, video in enumerate(videos, 1):
                caption = "🎬 Готово! Видео к публикации." if len(videos) == 1 else f"🎬 Часть {i}/{len(videos)}"
                try:
                    await message.answer_video(FSInputFile(video), caption=caption,
                                               reply_markup=after_video_kb() if i == len(videos) else None)
                except Exception as exc:
                    logger.error("Не удалось отправить видео %s: %s", video, exc)
            try:
                await status_msg.delete()
            except Exception:
                pass
            db.finish_job(job_id, "completed", output_path=str(videos[0]) if videos else "")

        except (CancellationError, asyncio.CancelledError):
            db.finish_job(job_id, "cancelled", error="Отменено пользователем")
            try:
                await status_msg.edit_text("❌ Генерация отменена.", reply_markup=main_menu_kb())
            except Exception:
                pass
        except UserError as exc:
            logger.warning("Ошибка пользователя [%s]: %s", job_id, exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"❌ {exc}", reply_markup=main_menu_kb())
        except ConfigurationError as exc:
            logger.error("Ошибка конфигурации [%s]: %s", job_id, exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"⚙️ Ошибка конфигурации: {exc}\n\nПроверьте .env и перезапустите бота.",
                                       reply_markup=main_menu_kb())
        except ValidationError as exc:
            logger.warning("Ошибка валидации [%s]: %s", job_id, exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"🛠 Результат не прошёл проверку: {exc}\n\nПопробуйте ещё раз.",
                                       reply_markup=main_menu_kb())
        except TTSError as exc:
            logger.error("TTS ошибка [%s]: %s", job_id, exc.details or exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"🔊 Ошибка озвучки: {exc}\n\nПопробуйте сократить текст или повторите позже.",
                                       reply_markup=main_menu_kb())
        except VideoSourceError as exc:
            logger.error("Ошибка видео-источника [%s]: %s", job_id, exc.details or exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"🎬 Ошибка подбора видео: {exc}\n\nПопробуйте другую новость или повторите позже.",
                                       reply_markup=main_menu_kb())
        except ProviderError as exc:
            logger.error("Ошибка провайдера [%s]: %s", job_id, exc.details or exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"🌐 Ошибка внешнего сервиса: {exc}\n\nПопробуйте повторить позже.",
                                       reply_markup=main_menu_kb())
        except ValueError as exc:
            logger.error("Ошибка пайплайна [%s]: %s", job_id, exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text(f"❌ {exc}", reply_markup=main_menu_kb())
        except InternalError as exc:
            logger.error("Внутренняя ошибка [%s]: %s", job_id, exc.details or exc)
            db.finish_job(job_id, "failed", error=str(exc))
            await status_msg.edit_text("❌ Внутренняя ошибка. Попробуйте ещё раз. Если повторится — проверьте логи.",
                                       reply_markup=main_menu_kb())
        except Exception as exc:
            logger.exception("Пайплайн упал [%s] для пользователя %s", job_id, message.from_user.id)
            db.finish_job(job_id, "failed", error=str(exc))
            try:
                await status_msg.edit_text("❌ Внутренняя ошибка. Попробуйте ещё раз. Если повторится — проверьте логи.",
                                           reply_markup=main_menu_kb())
            except Exception:
                pass
        finally:
            _cancel_tokens.pop(job_id, None)
            remove_tree(work_dir)


async def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")
    for err in config.validate_config():
        logger.warning("Конфигурация: %s", err)
    Path(config.WORK_DIR).mkdir(exist_ok=True)
    Path(config.OUTPUT_DIR).mkdir(exist_ok=True)
    Path(config.DATA_DIR).mkdir(exist_ok=True)
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())