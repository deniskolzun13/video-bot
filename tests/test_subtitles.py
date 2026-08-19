"""Unit tests for subtitles.py"""


from subtitles import (
    split_sentences,
    split_into_phrases,
    build_timings,
    _ass_time,
    _srt_time,
    _escape_ass,
)


class TestSplitSentences:
    def test_simple_sentences(self):
        text = "Первое предложение. Второе предложение! Третье предложение?"
        result = split_sentences(text)
        assert result == ["Первое предложение.", "Второе предложение!", "Третье предложение?"]

    def test_ellipsis(self):
        text = "Предложение с многоточием… И ещё одно."
        result = split_sentences(text)
        assert result == ["Предложение с многоточием…", "И ещё одно."]

    def test_multiple_spaces(self):
        text = "Первое.   Второе.    Третье."
        result = split_sentences(text)
        assert result == ["Первое.", "Второе.", "Третье."]

    def test_empty_string(self):
        result = split_sentences("")
        assert result == []

    def test_whitespace_only(self):
        result = split_sentences("   \n\t  ")
        assert result == []

    def test_no_punctuation(self):
        result = split_sentences("Текст без знаков препинания")
        assert result == ["Текст без знаков препинания"]


class TestSplitIntoPhrases:
    def test_simple_merge(self):
        text = "Короткое. Ещё одно. И третье."
        phrases = split_into_phrases(text, min_chars=20, max_chars=70)
        assert len(phrases) == 1
        assert "Короткое. Ещё одно. И третье." in phrases[0]

    def test_long_sentence_split_by_comma(self):
        text = "Длинное предложение, которое имеет много запятых, и поэтому должно быть разделено."
        phrases = split_into_phrases(text, min_chars=20, max_chars=40)
        # Должно быть разбито по запятым
        assert all(len(p) <= 40 for p in phrases)

    def test_hard_split_long_piece(self):
        text = "А" * 100  # Очень длинное слово без запятых
        phrases = split_into_phrases(text, min_chars=20, max_chars=30)
        # Должно быть разбито жёстко по max_chars
        assert all(len(p) <= 30 for p in phrases)
        assert len(phrases) > 1

    def test_short_text(self):
        text = "Короткий текст."
        phrases = split_into_phrases(text, min_chars=20, max_chars=70)
        assert len(phrases) == 1

    def test_empty_string(self):
        phrases = split_into_phrases("", min_chars=20, max_chars=70)
        assert phrases == [""]

    def test_whitespace_only(self):
        phrases = split_into_phrases("   \n\t  ", min_chars=20, max_chars=70)
        assert phrases == ["   \n\t  "]

    def test_min_chars_merge(self):
        text = "Один. Два. Три. Четыре. Пять."
        phrases = split_into_phrases(text, min_chars=10, max_chars=50)
        # Короткие предложения склеиваются, но "Пять." остаётся отдельно
        assert len(phrases) == 3
        assert "Один. Два." in phrases[0]
        assert "Три. Четыре." in phrases[1]
        assert "Пять." in phrases[2]


class TestBuildTimings:
    def test_equal_distribution(self):
        phrases = ["Одна.", "Две.", "Три."]
        timings = build_timings(phrases, 9.0)
        assert len(timings) == 3
        # "Одна." = 5 chars, "Две." = 4, "Три." = 4, итого 13 chars
        # Пропорционально: 5/13*9=3.46, 4/13*9=2.77, 4/13*9=2.77
        assert timings[0] == (0.0, 9.0 * 5 / 13)
        assert timings[1] == (9.0 * 5 / 13, 9.0 * 9 / 13)
        assert timings[2] == (9.0 * 9 / 13, 9.0)

    def test_proportional_to_length(self):
        phrases = ["Короткая.", "Очень длинная фраза для теста.", "Средняя."]
        timings = build_timings(phrases, 10.0)
        # Длинная фраза должна получить больше времени
        dur0 = timings[0][1] - timings[0][0]
        dur1 = timings[1][1] - timings[1][0]
        dur2 = timings[2][1] - timings[2][0]
        assert dur1 > dur0
        assert dur1 > dur2

    def test_zero_duration(self):
        phrases = ["Одна.", "Две."]
        timings = build_timings(phrases, 0.0)
        assert timings[0] == (0.0, 0.0)
        assert timings[1] == (0.0, 0.0)

    def test_single_phrase(self):
        timings = build_timings(["Единственная фраза."], 5.0)
        assert timings == [(0.0, 5.0)]


class TestAssTime:
    def test_basic(self):
        assert _ass_time(0.0) == "0:00:00.00"
        assert _ass_time(1.5) == "0:00:01.50"
        assert _ass_time(65.0) == "0:01:05.00"
        assert _ass_time(3661.25) == "1:01:01.25"

    def test_centisecond_rounding(self):
        # 0.995 -> 1.00 -> cs=100 -> carry to seconds
        assert _ass_time(0.995) == "0:00:01.00"
        # 1.999 -> cs=100
        assert _ass_time(1.999) == "0:00:02.00"


class TestSrtTime:
    def test_basic(self):
        assert _srt_time(0.0) == "00:00:00,000"
        assert _srt_time(1.5) == "00:00:01,500"
        assert _srt_time(65.0) == "00:01:05,000"
        assert _srt_time(3661.25) == "01:01:01,250"

    def test_millisecond_rounding(self):
        # 0.9995 -> 1000ms -> carry
        assert _srt_time(0.9995) == "00:00:01,000"
        assert _srt_time(1.9995) == "00:00:02,000"


class TestEscapeAss:
    def test_braces(self):
        assert _escape_ass("test {braces}") == "test \\{braces\\}"
        assert _escape_ass("{") == "\\{"
        assert _escape_ass("}") == "\\}"

    def test_nested(self):
        assert _escape_ass("{nested {braces}}") == "\\{nested \\{braces\\}\\}"

    def test_no_braces(self):
        assert _escape_ass("plain text") == "plain text"
        assert _escape_ass("") == ""


class TestNormalizeWord:
    def test_normalize_word(self):
        from subtitles.alignment import normalize_word
        assert normalize_word("Hello!") == "hello"
        assert normalize_word("GPT-5.6") == "gpt56"
        assert normalize_word("Open-AI") == "openai"
        assert normalize_word("  HELLO  ") == "hello"

    def test_normalize_unicode(self):
        from subtitles.alignment import normalize_word
        assert normalize_word("Café") == "café"
        assert normalize_word("ёлка") == "ёлка"

    def test_normalize_punctuation_only(self):
        from subtitles.alignment import normalize_word
        assert normalize_word("!!!") == ""
        assert normalize_word("") == ""


class TestFuzzyWordMatch:
    def test_gpt_numbers(self):
        from subtitles.alignment import fuzzy_word_match
        assert fuzzy_word_match("gpt", "gpt-4") is True
        assert fuzzy_word_match("gpt4", "gpt-4o") is True
        assert fuzzy_word_match("gpt-5.6", "gpt56") is True

    def test_hyphenated_words(self):
        from subtitles.alignment import fuzzy_word_match
        assert fuzzy_word_match("Open-AI", "openai") is True
        assert fuzzy_word_match("OpenAI", "open-ai") is True

    def test_not_match_different(self):
        from subtitles.alignment import fuzzy_word_match
        assert fuzzy_word_match("openai", "openaiv2") is False
        assert fuzzy_word_match("gpt", "claude") is False
        assert fuzzy_word_match("", "gpt") is False


class TestWhisperOptional:
    def test_alignment_returns_none_when_unavailable(self):
        """whisper не обязателен: если модель грузится с ошибкой — fallback."""
        import subtitles.alignment as al

        def _raise(*args, **kwargs):
            raise ImportError("no whisper")

        old = al._get_whisper_model
        try:
            al._get_whisper_model = _raise
            import asyncio
            res = asyncio.run(al.build_timings_aligned(["test"], "/tmp/nonexistent.wav"))
            assert res is None
        finally:
            al._get_whisper_model = old

    def test_word_level_fallback_chain(self):
        """word_level -> aligned -> proportional: цепочка в pipeline."""
        import asyncio
        from subtitles.alignment import build_timings_word_level

        word_ts = [
            {"word": "Привет", "start": 0.0, "end": 0.5},
            {"word": "мир", "start": 0.5, "end": 1.0},
        ]
        timings = asyncio.run(build_timings_word_level(["Привет мир"], word_ts))
        assert timings is not None
        assert abs(timings[0][0] - 0.0) < 0.01
        assert abs(timings[0][1] - 1.0) < 0.01

    def test_word_level_asr_mismatch(self):
        """ASR вернул расхождения (gpt vs gpt-4) — fuzzy спасает."""
        import asyncio
        from subtitles.alignment import build_timings_word_level

        word_ts = [
            {"word": "GPT-4", "start": 0.0, "end": 0.6},
            {"word": "good", "start": 0.6, "end": 1.0},
        ]
        timings = asyncio.run(build_timings_word_level(["gpt good"], word_ts))
        assert timings is not None
        assert abs(timings[0][0] - 0.0) < 0.01

    def test_word_level_fallback_proportional(self):
        """Если ASR не дал слов — пропорциональный тайминг работает."""
        from subtitles.alignment import build_timings
        timings = build_timings(["a", "bbb"], 4.0)
        assert abs(timings[0][1] - 1.0) < 0.01
        assert abs(timings[1][1] - 4.0) < 0.01