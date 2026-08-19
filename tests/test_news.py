"""Тесты новостного выпуска: models, parser, editor, dedup, ordering,
transitions, script, timeline, ASR-оффсеты, LocalVideoProvider, local-mode.

Все LLM/TTS/ASR-вызовы замоканы (мок только в тестах).
"""
import asyncio
import json
from pathlib import Path

import pytest

import config
from ai.base import LLMProvider
from news import (
    NewsBatch,
    NewsEditor,
    NewsItem,
    ScriptBuilder,
    SegmentAudio,
    TransitionPlanner,
    UnifiedScript,
    build_timeline,
    deduplicate,
    order_news,
    parse_news_batch,
    quality_check,
    split_news_from_messages,
    validate_batch,
)
from news.dedup import _normalize, _similarity
from news.models import ITEM_NEWS, ITEM_OUTRO, TimelineItem, UnifiedTimeline
from news.ordering import _extract_date, _interest_score
from news.timeline import _block_key
from video.local import LocalVideoProvider


# --- Моки ---
class FakeLLM(LLMProvider):
    """Стабильные JSON-ответы для всех промптов."""

    def __init__(self, json_result: dict | None = None):
        self.json_result = json_result or {
            "text": "Отредактированная новость.",
            "title": "Заголовок",
            "summary": "Краткое содержание.",
            "keywords": ["tech", "news"],
            "importance": 0.8,
            "category": "technology",
        }

    async def complete(self, prompt: str) -> str:
        if "transitions" in prompt:
            return '{"transitions": ["А теперь к следующей новости.", "И ещё одна важная тема."]}'
        if "вступление" in prompt:
            return '{"text": "Привет! Главные новости — сейчас."}'
        if "завершение" in prompt:
            return '{"text": "На сегодня всё. Пока!"}'
        return json.dumps(self.json_result, ensure_ascii=False)

    def name(self) -> str:
        return "fake"


@pytest.fixture
def news_items():
    return [
        NewsItem(id=1, original_text="Первая новость о технологиях и ИИ.",
                 edited_text="Первая новость о технологиях и ИИ.",
                 title="Новость 1", summary="Суммари 1",
                 keywords=["ai", "tech"], importance=0.9, category="ai"),
        NewsItem(id=2, original_text="Вторая новость про смартфоны.",
                 edited_text="Вторая новость про смартфоны.",
                 title="Новость 2", summary="Суммари 2",
                 keywords=["phone"], importance=0.7, category="tech"),
        NewsItem(id=3, original_text="Третья новость про бизнес.",
                 edited_text="Третья новость про бизнес.",
                 title="Новость 3", summary="Суммари 3",
                 keywords=["business"], importance=0.5, category="business"),
    ]


# --- NewsBatch / models ---
class TestNewsBatch:
    def test_news_by_id(self, news_items):
        batch = NewsBatch(batch_id="b1", news=news_items)
        assert batch.news_by_id(2).id == 2
        assert batch.news_by_id(99) is None

    def test_ordered_news(self, news_items):
        batch = NewsBatch(batch_id="b1", news=news_items, order=[3, 1])
        ordered = batch.ordered_news()
        assert [n.id for n in ordered] == [3, 1, 2]

    def test_to_dict_roundtrip(self, news_items):
        batch = NewsBatch(batch_id="b1", news=news_items)
        d = batch.to_dict()
        assert d["batch_id"] == "b1"
        assert len(d["news"]) == 3


class TestUnifiedTimeline:
    def test_no_overlap_gap_negative(self):
        timeline = UnifiedTimeline(items=[
            TimelineItem(id="i", type="intro", start=0, end=10, duration=10),
            TimelineItem(id="n", type=ITEM_NEWS, start=10, end=20, duration=10, news_id=1),
            TimelineItem(id="o", type=ITEM_OUTRO, start=20, end=25, duration=5),
        ])
        assert timeline.validate() == []
        assert timeline.duration == 25

    def test_overlap_detected(self):
        timeline = UnifiedTimeline(items=[
            TimelineItem(id="a", type="intro", start=0, end=10, duration=10),
            TimelineItem(id="b", type=ITEM_NEWS, start=9, end=20, duration=11, news_id=1),
        ])
        assert any("overlap" in e for e in timeline.validate())

    def test_negative_duration_detected(self):
        timeline = UnifiedTimeline(items=[
            TimelineItem(id="a", type="intro", start=5, end=5, duration=-1),
        ])
        assert any("negative" in e for e in timeline.validate())


# --- Parser ---
class TestNewsParser:
    def test_parse_delimited(self):
        text = "=== NEWS 1 ===\nПервая новость.\n=== NEWS 2 ===\nВторая новость."
        items = parse_news_batch(text)
        assert len(items) == 2
        assert items[0].startswith("Первая новость")

    def test_parse_headers(self):
        text = "Новость 1:\nПервая новость про ИИ.\nНовость 2:\nВторая новость про игры."
        items = parse_news_batch(text)
        assert len(items) == 2
        assert items[0].startswith("Первая новость")

    def test_split_from_messages(self):
        msgs = ["Короткое", "Это полноценная новость длиной достаточно.",
                "И ещё одна новость достаточно длинная."]
        items = split_news_from_messages(msgs)
        assert len(items) == 2
        assert all(len(m) >= 30 for m in items)

    def test_validate_batch_limits(self):
        many = ["x" * 20 for _ in range(config.MAX_NEWS_PER_BATCH + 1)]
        errors = validate_batch(many)
        assert any("новостей" in e for e in errors)

    def test_validate_batch_length(self):
        huge = ["x" * (config.MAX_NEWS_TEXT_LENGTH + 10)]
        errors = validate_batch(huge)
        assert errors

    def test_parse_empty(self):
        assert parse_news_batch("") == []


# --- Editor ---
class TestNewsEditor:
    async def test_edit_with_llm(self):
        editor = NewsEditor(FakeLLM())
        item = await editor.edit(1, "Исходная новость, которую нужно отредактировать.")
        assert item.id == 1
        assert item.edited_text
        assert item.title
        assert item.keywords
        assert 0 < item.importance <= 1.0

    async def test_edit_fallback_on_invalid_json(self):
        class BadLLM(FakeLLM):
            async def complete(self, prompt):
                return "не json"

        editor = NewsEditor(BadLLM())
        item = await editor.edit(1, "Новость, которая не распознается.")
        # fallback: оригинальный текст
        assert item.edited_text == "Новость, которая не распознается."

    async def test_quality_check_rejects_fabricated_numbers(self):
        item = NewsItem(id=1, original_text="Компания сообщила о росте на 10% за год.",
                        edited_text="Компания сообщила о росте на 99% за год.")
        assert not await quality_check(item)
        item2 = NewsItem(id=2, original_text="Компания сообщила о росте на 99% за год.",
                         edited_text="Компания сообщила о росте на 99% за год.")
        assert await quality_check(item2)


# --- Deduplication ---
class TestDedup:
    def test_normalize(self):
        assert _normalize("Hello, World!") == "hello world"
        assert _normalize("Технологии — будущее.") == "технологии будущее"

    def test_similarity(self):
        assert _similarity("один два три", "один два три") > 0.9
        assert _similarity("один два три", "совсем другое") < 0.5

    def test_deduplicate(self):
        items = [
            NewsItem(id=1, original_text="Один и тот же текст новости."),
            NewsItem(id=2, original_text="Один и тот же текст новости."),
            NewsItem(id=3, original_text="Совсем другая новость."),
        ]
        unique, removed = deduplicate(items)
        assert len(unique) == 2
        assert removed == [2]

    def test_deduplicate_no_removals(self):
        items = [
            NewsItem(id=1, original_text="Один текст."),
            NewsItem(id=2, original_text="Совсем другой текст."),
        ]
        unique, removed = deduplicate(items)
        assert len(unique) == 2
        assert removed == []


# --- Ordering ---
class TestOrdering:
    def test_extract_date(self):
        assert _extract_date("5 марта 2026 компания заявила") is not None
        assert _extract_date("Сегодня без даты") is None

    def test_interest_score(self):
        ai_item = NewsItem(id=1, edited_text="Новость про ИИ.", keywords=["ai", "tech", "gpu"])
        plain_item = NewsItem(id=2, edited_text="Новость.", keywords=[])
        assert _interest_score(ai_item) > _interest_score(plain_item)

    def test_order_importance(self, news_items):
        # все без дат -> сортировка по важности
        ordered = order_news(news_items)
        assert ordered[0] == 1
        assert ordered[-1] == 3


# --- Transitions ---
class TestTransitionPlanner:
    async def test_template_fallback(self, news_items):
        planner = TransitionPlanner(FakeLLM())
        transitions = await planner.plan(news_items[:2], use_llm=False)
        assert len(transitions) == 1
        assert transitions[0].from_id == news_items[0].id
        assert transitions[0].to_id == news_items[1].id
        assert transitions[0].text

    async def test_single_news_no_transitions(self, news_items):
        planner = TransitionPlanner(FakeLLM())
        assert await planner.plan(news_items[:1]) == []

    async def test_llm_transitions(self, news_items):
        planner = TransitionPlanner(FakeLLM())
        transitions = await planner.plan(news_items[:3], use_llm=True)
        assert len(transitions) == 2
        assert all(t.text for t in transitions)


# --- ScriptBuilder ---
class TestScriptBuilder:
    async def test_build_structure(self, news_items):
        from news.models import Transition

        transitions = [Transition(from_id=1, to_id=2, text="Переход 1")]
        builder = ScriptBuilder(FakeLLM())
        script = await builder.build(news_items, transitions, use_llm=True)
        assert script.blocks[0][0] == "intro"
        assert any(b[0] == "transition" for b in script.blocks)
        assert script.blocks[-1][0] == "outro"
        news_block = [b for b in script.blocks if b[0] == "news"]
        assert len(news_block) == 3

    async def test_full_text(self, news_items):
        builder = ScriptBuilder(FakeLLM())
        script = await builder.build(news_items, [], use_llm=False)
        assert "Первая новость" in script.full_text


# --- Timeline ---
class TestTimeline:
    def test_build_timeline_offsets(self):
        script = UnifiedScript(
            intro="Вступление", outro="Заключение",
            blocks=[
                ("intro", None, "Вступление"),
                ("news", 1, "Новость 1"),
                ("transition", 1, "Переход"),
                ("news", 2, "Новость 2"),
                ("outro", None, "Заключение"),
            ],
        )
        seg = {
            "intro": SegmentAudio("intro", "Вступление", "a.mp3", 2.0),
            "news:1": SegmentAudio("news:1", "Новость 1", "n1.mp3", 3.0),
            "transition:1": SegmentAudio("transition:1", "Переход", "t.mp3", 0.5),
            "news:2": SegmentAudio("news:2", "Новость 2", "n2.mp3", 2.5),
            "outro": SegmentAudio("outro", "Заключение", "o.mp3", 1.5),
        }
        timeline = build_timeline(script, seg, news_titles={1: "Заг", 2: "Заг2"})
        assert timeline.validate() == []
        # Нет overlap, нет gap: старт следующего == концу предыдущего
        for a, b in zip(timeline.items, timeline.items[1:]):
            assert abs(b.start - a.end) < 1e-6
        # Title card добавляет NEWS_TITLE_DURATION к news-сегментам
        news_items = [i for i in timeline.items if i.type == ITEM_NEWS]
        assert news_items
        for n in news_items:
            assert n.duration >= config.NEWS_TITLE_DURATION

    def test_build_timeline_exact_expected_case(self):
        """Тестовый сценарий из ТЗ: NEWS 1=10с, TRANSITION=0.5, NEWS 2=20с,
        TRANSITION=0.5, NEWS 3=15с."""
        script = UnifiedScript(
            intro="", outro="",
            blocks=[
                ("news", 1, "N1"), ("transition", 1, "T1"),
                ("news", 2, "N2"), ("transition", 2, "T2"),
                ("news", 3, "N3"),
            ],
        )
        seg = {
            "news:1": SegmentAudio("news:1", "N1", "a.mp3", 10.0),
            "transition:1": SegmentAudio("transition:1", "T1", "t.mp3", 0.5),
            "news:2": SegmentAudio("news:2", "N2", "b.mp3", 20.0),
            "transition:2": SegmentAudio("transition:2", "T2", "t2.mp3", 0.5),
            "news:3": SegmentAudio("news:3", "N3", "c.mp3", 15.0),
        }
        timeline = build_timeline(script, seg, news_titles={1: "t", 2: "t", 3: "t"})
        assert timeline.validate() == []
        expected_duration = 10 + 0.5 + 20 + 0.5 + 15
        # + 3 * NEWS_TITLE_DURATION для трёх news title cards
        assert abs(timeline.duration - (expected_duration + 3 * config.NEWS_TITLE_DURATION)) < 1e-6

    def test_block_key(self):
        assert _block_key("news", 5) == "news:5"
        assert _block_key("transition", 5) == "transition:5"
        assert _block_key("intro", None) == "intro"


# --- ASR offsets (субтитры с offset таймлайна) ---
class TestSubtitleOffsets:
    def test_phrase_timings_relative(self):
        """Тайминги фраз относительны аудио; абсолютный сдвиг добавляет
        render (sub_start + rel). Проверяем, что rel всегда >= 0 и монотонны."""
        from subtitles import build_timings

        phrases = ["Фраза первая", "Фраза вторая", "Фраза третья"]
        rel = build_timings(phrases, 6.0)
        assert len(rel) == len(phrases)
        assert rel[0][0] == 0.0
        for (a1, e1), (a2, e2) in zip(rel, rel[1:]):
            assert a2 >= e1 - 1e-6
        assert rel[-1][1] <= 6.0 + 1e-6

    def test_segment_offset_applied_in_render(self, monkeypatch):
        """Оффсет сегмента применяется к ASS: проверяем через _ass_for_timeline."""
        from video_render_unified import _ass_for_timeline

        news_titles = {1: "Заголовок"}
        timeline = UnifiedTimeline(items=[
            TimelineItem(id="n1", type=ITEM_NEWS, start=100.0, end=105.0,
                         duration=5.0, news_id=1, text="Тестовая фраза для субтитров.",
                         audio_path="x.mp3", phrase_timings=[(0.0, 5.0)]),
        ])
        out = Path("/tmp/test_subs.ass")
        _ass_for_timeline(timeline, out, news_titles)
        content = out.read_text(encoding="utf-8")
        # субтитры должны начинаться после 100с (offset) + title card
        assert "100:0" in content or "1:40" in content


# --- LocalVideoProvider ---
class TestLocalVideoProvider:
    def test_search_returns_none_when_no_dir(self, tmp_path):
        provider = LocalVideoProvider(media_dir=str(tmp_path / "nope"))
        clips = asyncio.run(provider.search("ai"))
        assert clips == []

    def test_search_finds_by_keyword(self, tmp_path):
        media = tmp_path / "media"
        (media / "ai").mkdir(parents=True)
        clip = media / "ai" / "neural_network.mp4"
        clip.write_bytes(b"fake-video")
        provider = LocalVideoProvider(media_dir=str(media))
        clips = asyncio.run(provider.search("neural"))
        assert clips
        assert clips[0].id.startswith("local:ai:")

    def test_download_copies_file(self, tmp_path):
        media = tmp_path / "media"
        (media / "technology").mkdir(parents=True)
        src = media / "technology" / "clip.mp4"
        src.write_bytes(b"abc")
        provider = LocalVideoProvider(media_dir=str(media))
        clips = asyncio.run(provider.search("clip"))
        assert clips
        dest = tmp_path / "out.mp4"
        asyncio.run(provider.download(clips[0], dest))
        assert dest.exists()
        assert dest.read_bytes() == b"abc"

    def test_download_missing_raises(self, tmp_path):
        from video_source import VideoClip

        provider = LocalVideoProvider(media_dir=str(tmp_path / "media"))
        clip = VideoClip(id="local:x", url=str(tmp_path / "missing.mp4"),
                         width=0, height=0, duration=0, query="x")
        with pytest.raises(ValueError):
            asyncio.run(provider.download(clip, tmp_path / "out.mp4"))


# --- Local mode: нет cloud fallback ---
class TestLocalMode:
    def test_factory_returns_ollama_in_local_mode(self, monkeypatch):
        monkeypatch.setattr(config, "AI_MODE", "local")
        from ai import create_llm_provider

        provider = create_llm_provider()
        from ai.ollama import OllamaProvider

        assert isinstance(provider, OllamaProvider)

    def test_ollama_url_guard(self, monkeypatch):
        from ai.ollama import _assert_local_url, OllamaProvider
        from utils.errors import ConfigurationError

        with pytest.raises(ConfigurationError):
            _assert_local_url("https://evil.example.com")
        with pytest.raises(ConfigurationError):
            OllamaProvider(base_url="https://evil.example.com", model="x")

    def test_ollama_local_url_allowed(self, monkeypatch):
        from ai.ollama import _assert_local_url

        assert _assert_local_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"