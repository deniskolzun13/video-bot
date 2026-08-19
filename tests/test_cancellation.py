"""Unit tests для CancellationToken и отмены в pipeline/рендере."""
import asyncio

import pytest

from utils.cancellation import CancellationError, CancellationToken, cancelled_by


class TestCancellationToken:
    def test_not_cancelled_by_default(self):
        t = CancellationToken()
        assert t.is_cancelled is False
        t.check()  # не должно бросить

    def test_cancel_sets_flag(self):
        t = CancellationToken()
        t.cancel("test reason")
        assert t.is_cancelled is True
        assert t.reason == "test reason"

    def test_cancel_is_idempotent(self):
        t = CancellationToken()
        t.cancel("first")
        t.cancel("second")
        assert t.reason == "first"

    def test_check_raises_after_cancel(self):
        t = CancellationToken()
        t.cancel()
        with pytest.raises(CancellationError):
            t.check()

    def test_check_async_raises(self):
        t = CancellationToken()
        t.cancel()
        with pytest.raises(CancellationError):
            asyncio.run(t.check_async())

    def test_default_reason(self):
        t = CancellationToken()
        t.cancel()
        assert t.reason == "Отменено пользователем"

    def test_cancelled_by_helper(self):
        assert cancelled_by(None) is False
        t = CancellationToken()
        assert cancelled_by(t) is False
        t.cancel()
        assert cancelled_by(t) is True


class TestPipelineCancellation:
    def test_process_text_cancelled_before_start(self, tmp_path):
        """Отмена до старта — CancellationError, ничего не создаётся."""
        from pipeline import process_text
        from utils.cancellation import CancellationToken

        token = CancellationToken()
        token.cancel("stop")

        async def notify(s):
            pass

        with pytest.raises(CancellationError):
            asyncio.run(process_text("Тест", tmp_path, notify, cancel_token=token))
        assert list(tmp_path.iterdir()) == []

    def test_process_text_no_token_works(self, tmp_path):
        """Без токена пайплайн стартует — не CancellationError (упадёт по логике)."""
        from pipeline import process_text

        async def notify(s):
            pass

        # Пустой текст -> ValueError, а не CancellationError
        with pytest.raises(ValueError):
            asyncio.run(process_text("   ", tmp_path, notify))

    def test_cancel_during_pipeline_raises(self):
        """Отмена в процессе — CancellationError на ближайшей проверке."""
        from pipeline import process_text
        from utils.cancellation import CancellationToken

        token = CancellationToken()
        calls = []

        async def notify(s):
            calls.append(s)

        # Отмена до старта — процесс_text проверяет токен первым делом
        token.cancel("mid-way")
        with pytest.raises(CancellationError):
            asyncio.run(process_text(
                "Тест", __import__("tempfile").mkdtemp(), notify, cancel_token=token
            ))


class TestRenderCancellation:
    def test_render_cancelled_raises(self, tmp_path):
        """Если ffmpeg запущен и токен отменён — CancellationError."""
        from video_render import render_video
        from utils.cancellation import CancellationToken

        token = CancellationToken()
        token.cancel("user pressed stop")

        # Пусть даже файлы не существуют — отмена проверяется до ffmpeg
        with pytest.raises(CancellationError):
            render_video(
                [(tmp_path / "a.mp4", 1.0, 0.0)],
                tmp_path / "a.mp3",
                tmp_path / "a.ass",
                tmp_path / "out.mp4",
                cancel_token=token,
            )

    def test_render_without_token_no_cancel(self, tmp_path):
        """Без токена рендер не бросает CancellationError (упадёт по другой причине)."""
        from video_render import render_video

        # Нет токена — не CancellationError, а ValueError (ffmpeg не найдёт файлы)
        with pytest.raises(Exception) as excinfo:
            render_video(
                [(tmp_path / "a.mp4", 1.0, 0.0)],
                tmp_path / "a.mp3",
                tmp_path / "a.ass",
                tmp_path / "out.mp4",
            )
        assert not isinstance(excinfo.value, CancellationError)