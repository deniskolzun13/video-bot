"""Пакет news: обработка батча новостей локальной LLM.

NewsBatch -> NewsEditor (PHASE 1) -> dedup -> ordering -> transitions -> script.
"""
from news.models import (
    ITEM_INTRO,
    ITEM_NEWS,
    ITEM_OUTRO,
    ITEM_TRANSITION,
    NewsBatch,
    NewsItem,
    TimelineItem,
    Transition,
    UnifiedScript,
    UnifiedTimeline,
)
from news.parser import parse_news_batch, split_news_from_messages, validate_batch
from news.editor import NewsEditor, quality_check, validate_facts
from news.dedup import deduplicate
from news.ordering import NewsOrderingService, order_news
from news.transitions import TransitionPlanner
from news.script import ScriptBuilder
from news.timeline import SegmentAudio, build_timeline

__all__ = [
    "NewsBatch",
    "NewsItem",
    "Transition",
    "TimelineItem",
    "UnifiedScript",
    "UnifiedTimeline",
    "ITEM_INTRO",
    "ITEM_NEWS",
    "ITEM_TRANSITION",
    "ITEM_OUTRO",
    "parse_news_batch",
    "split_news_from_messages",
    "validate_batch",
    "NewsEditor",
    "quality_check",
    "validate_facts",
    "deduplicate",
    "NewsOrderingService",
    "order_news",
    "TransitionPlanner",
    "ScriptBuilder",
    "SegmentAudio",
    "build_timeline",
]