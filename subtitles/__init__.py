"""Пакет субтитров.

Обратная совместимость: все имена, которые раньше экспортировал subtitles.py,
реэкспортируются здесь, поэтому `from subtitles import split_sentences` и т.п.
продолжают работать без изменений.
"""
from subtitles.alignment import (
    build_timings,
    build_timings_aligned,
    build_timings_word_level,
    words_to_phrase_timings,
)
from subtitles.generator import (
    SENTENCE_SPLIT,
    _ass_time,
    _build_karaoke_line,
    _escape_ass,
    _highlight_keywords,
    _srt_time,
    generate_ass,
    generate_srt,
    split_into_phrases,
    split_sentences,
)
from subtitles.styles import PRESETS, SubtitleStyle, get_subtitle_style

__all__ = [
    "split_sentences",
    "split_into_phrases",
    "build_timings",
    "build_timings_aligned",
    "build_timings_word_level",
    "words_to_phrase_timings",
    "generate_ass",
    "generate_srt",
    "SENTENCE_SPLIT",
    "_ass_time",
    "_srt_time",
    "_escape_ass",
    "_highlight_keywords",
    "_build_karaoke_line",
    "PRESETS",
    "SubtitleStyle",
    "get_subtitle_style",
]