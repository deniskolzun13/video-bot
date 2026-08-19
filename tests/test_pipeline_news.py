"""Интеграционный тест process_news_batch с моками LLM/TTS/ASR/видео/рендера.

Проверяет связность: редактирование -> dedup -> порядок -> script -> TTS ->
таймлайн -> видео -> рендер (один mp4).
"""
import asyncio
from pathlib import Path

import pytest

import config
import pipeline_news
from ai.base import LLMProvider


class FakeNewsLLM(LLMProvider):
    """Возвращает валидный JSON редактирования на любой промпт."""

    async def complete(self, prompt: str) -> str:
        if "transitions" in prompt:
            return '{"transitions": ["А теперь к следующей.", "И ещё одно."]}'
        if "вступление" in prompt:
            return '{"text": "Привет! Смотри выпуск."}'
        if "завершение" in prompt:
            return '{"text": "Вот и всё. Пока!"}'
        return (
            '{"edited_text": "Отредактированная новость достаточно длинная для озвучки и субтитров.", '
            '"title": "Заголовок", "summary": "Краткое содержание.", '
            '"keywords": ["tech", "news", "ai"], "importance": 0.9, "category": "tech"}'
        )

    def name(self) -> str:
        return "fake-news"


async def _fake_synthesize_local(text, work_dir, voice="", speed=None, engine=""):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(work_dir.iterdir()))
    audio = work_dir / f"tts_{n}.mp3"
    audio.write_bytes(b"fake-audio")
    return audio, 2.0


async def _fake_align_timeline(timeline):
    return None


async def _fake_prepare_video_for_segment(item, keywords, provider, work_dir, index, used_ids, work_root):
    """Без реального ffmpeg: сегмент без клипов (фон создаст мок-рендер)."""
    return []


def _fake_render_unified(timeline, ass_path, out_path, **kwargs):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"fake-mp4")


@pytest.fixture
def news_fakes(monkeypatch, tmp_path):
    config.WORK_DIR = str(tmp_path / "work")
    config.OUTPUT_DIR = str(tmp_path / "output")
    config.JOB_DIR = str(tmp_path / "data" / "jobs")
    config.LOCAL_MEDIA_DIR = str(tmp_path / "media")
    config.VIDEO_SOURCE = "local"  # без сети
    config.NEWS_TITLE_ENABLED = False  # ускоряет и упрощает таймлайн

    monkeypatch.setattr(pipeline_news, "create_llm_provider", lambda: FakeNewsLLM())
    monkeypatch.setattr(pipeline_news, "_get_tts", lambda: _fake_synthesize_local)
    monkeypatch.setattr("news.asr.align_timeline", _fake_align_timeline)
    monkeypatch.setattr(
        "pipeline_news._prepare_video_for_segment", _fake_prepare_video_for_segment
    )
    monkeypatch.setattr(
        "pipeline_news.render_unified_timeline", _fake_render_unified
    )
    yield tmp_path


class TestNewsPipeline:
    async def test_full_news_batch(self, news_fakes):
        videos = await pipeline_news.process_news_batch(
            ["Первая новость достаточно длинная для обработки.",
             "Вторая новость тоже достаточно длинная для обработки.",
             "Третья новость ещё одна достаточно длинная для обработки."],
            work_dir=news_fakes / "work",
            notify=lambda s: asyncio.sleep(0),
            job_id="JOB-NEWS-1",
            user_id=1,
        )
        assert videos
        assert len(videos) == 1
        assert Path(videos[0]).exists()

    async def test_single_news_still_works(self, news_fakes):
        """Одна новость тоже проходит через NewsBatch-пайплайн."""
        videos = await pipeline_news.process_news_batch(
            ["Одна новость достаточно длинная для обработки выпуска."],
            work_dir=news_fakes / "work2",
            notify=lambda s: asyncio.sleep(0),
            job_id="JOB-NEWS-2",
            user_id=1,
        )
        assert videos
        assert len(videos) == 1

    async def test_validation_errors(self, news_fakes):
        from utils.errors import UserError

        # пустой список новостей -> UserError
        with pytest.raises(UserError):
            await pipeline_news.process_news_batch(
                [],
                work_dir=news_fakes / "work3",
                notify=lambda s: asyncio.sleep(0),
            )
        # слишком много новостей -> UserError
        many = ["новость длиной достаточно" for _ in range(config.MAX_NEWS_PER_BATCH + 1)]
        with pytest.raises(UserError):
            await pipeline_news.process_news_batch(
                many,
                work_dir=news_fakes / "work4",
                notify=lambda s: asyncio.sleep(0),
            )