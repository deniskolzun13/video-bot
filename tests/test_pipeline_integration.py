"""Интеграционный тест пайплайна с фейками внешних сервисов.

Замоканы: LLM, TTS, ASR, видео-провайдер, рендер. Проверяет связность
всех этапов process_text: анализ → сцены → озвучка → тайминг → подбор
клипов → субтитры → рендер → валидация → история.
"""
import asyncio
from pathlib import Path

import pytest

import ai
import pipeline
import config
import subtitles
import tts
from ai.base import LLMProvider
from script.analyzer import Analysis
from script.scene_planner import Scene, ScenePlan
from subtitles import split_into_phrases
from video_source import PexelsProvider


class FakeLLM(LLMProvider):
    """Возвращает стабильные JSON-ответы на любой промпт."""

    async def complete(self, prompt: str) -> str:
        if "Проанализируй новость" in prompt:
            return (
                '{"topic": "игры", "title": "Новая игра", '
                '"main_subject": "игра", "category": "games", '
                '"entities": ["Game"], "keywords": ["игра", "гейминг"], '
                '"visual_keywords": ["gaming", "computer"]}'
            )
        if "режиссёр вертикальных видео" in prompt:
            return (
                '{"scenes": [{"phrase_indexes": [0], "visual": "gaming setup", '
                '"keywords": ["gaming", "setup"], "duration_hint": 4}]}'
            )
        return "{}"

    def name(self) -> str:
        return "fake"


async def _fake_analyze(text: str, provider=None) -> Analysis:
    return Analysis(
        topic="игры", title="Новая игра", main_subject="игра", category="games",
        entities=["Game"], keywords=["игра", "гейминг"],
        visual_keywords=["gaming", "computer"],
    )


async def _fake_plan_scenes(text: str, analysis: Analysis, provider=None, max_scenes=12) -> ScenePlan:
    return ScenePlan(scenes=[
        Scene(visual="gaming setup", keywords=["gaming"], duration_hint=4.0, phrase_indexes=[0]),
    ])


async def _fake_synthesize(text: str, work_dir: Path, voice=None, speed=None):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = work_dir / "tts_audio.mp3"
    audio.write_bytes(b"fake-mp3")
    return audio, 3.0


async def _fake_get_word_timestamps(audio_path: Path, text: str = "") -> list[dict] | None:
    return None


async def _fake_build_timings_aligned(phrases, audio_path) -> list[tuple[float, float]] | None:
    return [(0.0, 1.0) for _ in phrases]


async def _fake_build_provider(text: str, settings=None):
    return PexelsProvider("fake-key")


async def _fake_select_clips(phrases, timings, provider, work_dir, scenes=None):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i in range(len(phrases)):
        clip = work_dir / f"clip_{i}.mp4"
        clip.write_bytes(b"fake-clip")
        clips.append((clip, 0.0, 2.0))
    return clips


def _fake_render_video(clips, audio_path, ass_path, out_path, **kwargs):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"fake-mp4")


def _fake_validate_output(path, target_w=None, target_h=None):
    return {
        "ok": True, "reasons": [],
        "duration": 3.0, "resolution": (target_w or config.VIDEO_WIDTH, target_h or config.VIDEO_HEIGHT),
    }


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Мокаем все внешние зависимости pipeline на фейки."""
    config.WORK_DIR = str(tmp_path / "work")
    config.OUTPUT_DIR = str(tmp_path / "output")
    config.JOB_DIR = str(tmp_path / "data" / "jobs")
    config.SCRIPT_GENERATION = "off"
    config.SUB_KARAOKE = False
    config.MAX_VIDEO_DURATION = 60.0

    monkeypatch.setattr(ai, "create_llm_provider", lambda: FakeLLM())
    monkeypatch.setattr(pipeline, "analyze_text", _fake_analyze)
    monkeypatch.setattr(pipeline, "plan_scenes", _fake_plan_scenes)
    monkeypatch.setattr(tts, "synthesize", _fake_synthesize)
    monkeypatch.setattr(tts, "get_word_timestamps", _fake_get_word_timestamps)
    monkeypatch.setattr(subtitles, "build_timings_aligned", _fake_build_timings_aligned)
    monkeypatch.setattr(pipeline, "_build_provider", _fake_build_provider)
    monkeypatch.setattr(pipeline, "_select_clips", _fake_select_clips)
    monkeypatch.setattr(pipeline, "render_video", _fake_render_video)
    monkeypatch.setattr(pipeline, "validate_output", _fake_validate_output)
    yield tmp_path


class TestPipelineIntegration:
    async def test_full_pipeline(self, fakes):
        videos = await pipeline.process_text(
            "Сегодня вышла новая игра. Геймеры в восторге.",
            work_dir=fakes / "work",
            notify=lambda s: asyncio.sleep(0),
            task_id="testtask",
            job_id="JOB-INT-1",
            user_id=1,
        )
        assert videos, "должен быть минимум один видеофайл"
        for v in videos:
            assert Path(v).exists()
            assert Path(v).read_bytes() == b"fake-mp4"

    async def test_pipeline_writes_job_dirs(self, fakes):
        job_root = fakes / "data" / "jobs" / "JOB-INT-2"
        videos = await pipeline.process_text(
            "Простая новость для проверки структуры.",
            work_dir=job_root / "input",
            notify=lambda s: asyncio.sleep(0),
            task_id="task2",
            job_id="JOB-INT-2",
            user_id=2,
            job_dir=job_root,
        )
        assert videos
        for sub in ("tts", "video", "subtitles"):
            assert (job_root / sub).is_dir(), f"нет поддиректории {sub}"
        assert (job_root / "part_0").is_dir()
        assert (job_root / "subtitles" / "subs.ass").exists()

    async def test_cancel_between_stages(self, fakes):
        from utils.cancellation import CancellationToken, CancellationError

        calls = {"n": 0}

        async def cancelling_notify(status: str) -> None:
            calls["n"] += 1
            if calls["n"] == 3:
                token.cancel()

        token = CancellationToken()
        with pytest.raises(CancellationError):
            await pipeline.process_text(
                "Новость, которая будет отменена в середине пайплайна.",
                work_dir=fakes / "work2",
                notify=cancelling_notify,
                task_id="task3",
                cancel_token=token,
            )

    async def test_format_resolution_respected(self, fakes, monkeypatch):
        calls = {}

        def spy_render(clips, audio, ass, out, **kwargs):
            calls.update(kwargs)
            _fake_render_video(clips, audio, ass, out, **kwargs)

        monkeypatch.setattr(pipeline, "render_video", spy_render)
        await pipeline.process_text(
            "Новость в квадратном формате.",
            work_dir=fakes / "work3",
            notify=lambda s: asyncio.sleep(0),
            task_id="task4",
            settings={"format": "square"},
        )
        assert calls.get("width") == 1080
        assert calls.get("height") == 1080

    def test_split_into_phrases_works_with_pipeline(self):
        phrases = split_into_phrases("Первая фраза. Вторая фраза для проверки.")
        assert len(phrases) >= 1