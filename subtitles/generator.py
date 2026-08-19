"""Генерация ASS/SRT-субтитров, разбивка на фразы, стили, highlight, karaoke."""
import logging
import re
from pathlib import Path

import config
from subtitles.styles import get_subtitle_style

logger = logging.getLogger(__name__)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def split_into_phrases(text: str, min_chars: int = 20, max_chars: int = 70) -> list[str]:
    """Делит текст на смысловые фразы для субтитров:
    короткие предложения склеиваются, длинные режутся по запятым, затем жёстко."""
    phrases: list[str] = []
    buf = ""

    def flush():
        nonlocal buf
        if buf:
            phrases.append(buf)
            buf = ""

    for sentence in split_sentences(text):
        if len(sentence) > max_chars:
            flush()
            pieces = [p.strip() for p in re.split(r"(?<=,)\s+", sentence) if p.strip()]
            for piece in pieces:
                if len(piece) > max_chars:
                    flush()
                    while piece:
                        phrases.append(piece[:max_chars])
                        piece = piece[max_chars:]
                else:
                    buf = f"{buf} {piece}".strip()
                    if len(buf) >= min_chars:
                        flush()
        else:
            buf = f"{buf} {sentence}".strip()
            if len(buf) >= min_chars:
                flush()
    flush()
    return phrases or [text]


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_ass(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def _highlight_keywords(text: str, keywords: list[str]) -> str:
    """Окружает ключевые слова и числа в тексте ASS override-тегами цвета.
    Числа и слова из keywords выделяются цветом SUB_HIGHLIGHT_COLOR."""
    if not config.SUB_HIGHLIGHT_KEYWORDS:
        return _escape_ass(text)

    # Список триггеров для выделения (целые слова из ключевых тем)
    triggers = set()
    for kw in keywords:
        triggers.update(w.lower() for w in re.findall(r"\b\w+\b", kw))

    # Токенизация: пробелы / числа (с десятичной точкой и % °) / слова / пунктуация.
    # Числа идут до \w+, чтобы "4.5" и "79%" не разбивались на части.
    token_pattern = re.compile(
        r"\s+|\b\d+(?:[.,]\d+)?(?:%|°)?|\w+|[^\w\s]",
        re.UNICODE | re.IGNORECASE,
    )
    result = []
    for match in token_pattern.finditer(text):
        token = match.group()
        if token.strip() and (token.strip()[0].isdigit() or token.lower().strip() in triggers):
            # Не экранируем { } внутри тегов, экранируем только в обычном тексте
            clean = _escape_ass(token)
            result.append(f"{{\\c{config.SUB_HIGHLIGHT_COLOR}}}{clean}{{\\c{config.SUB_PRIMARY}}}")
        else:
            result.append(_escape_ass(token))

    return "".join(result)


def _build_karaoke_line(phrase: str, word_timings: list[tuple[str, float, float]]) -> str:
    """Собирает строку с Karaoke-анимацией (\\k-теги в ASS).
    word_timings: [(word, start_time, end_time)] для каждого слова в фразе."""
    if not word_timings:
        return _escape_ass(phrase)

    parts: list[str] = []
    pos = 0
    for word, w_start, w_end in word_timings:
        # Пропускаем текст между словами (пробелы, пунктуация)
        idx = phrase.find(word, pos)
        if idx < 0:
            # Не нашли слово — добавляем как есть
            gap = phrase[pos:]
            if gap:
                parts.append(_escape_ass(gap))
            break

        gap = phrase[pos:idx]
        if gap:
            parts.append(_escape_ass(gap))

        duration_cs = int(round((w_end - w_start) * 100))
        parts.append(f"{{\\k{duration_cs}}}{_escape_ass(word)}")
        pos = idx + len(word)

    remainder = phrase[pos:]
    if remainder:
        parts.append(_escape_ass(remainder))

    return "".join(parts)


def generate_ass(phrases: list[str], timings: list[tuple[float, float]], path: Path, keywords: list[str] | None = None, word_timings_per_phrase: list[list[tuple[str, float, float]]] | None = None, style_name: str | None = None) -> Path:
    style = get_subtitle_style(style_name)
    header = (
        "[Script Info]\n"
        f"ScriptType: v4.00+\n"
        f"PlayResX: {config.VIDEO_WIDTH}\n"
        f"PlayResY: {config.VIDEO_HEIGHT}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Main,{style.fontname},{style.fontsize},{style.primary},"
        f"{style.secondary},{style.outline},{style.outline},"
        f"{style.bold},0,0,0,100,100,0,0,1,{style.outline_width},{style.shadow},"
        f"{style.alignment},{style.margin_l},{style.margin_r},{style.margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    use_karaoke = getattr(config, "SUB_KARAOKE", False) or style.karaoke
    use_highlight = getattr(config, "SUB_HIGHLIGHT_KEYWORDS", False) or style.highlight
    lines = []
    for i, ((start, end), phrase) in enumerate(zip(timings, phrases)):
        if use_karaoke and word_timings_per_phrase and i < len(word_timings_per_phrase):
            # Karaoke mode: использовать \k-теги для анимации появления слов
            line_text = _build_karaoke_line(phrase, word_timings_per_phrase[i])
        elif use_highlight:
            line_text = _highlight_keywords(phrase, keywords or [])
        else:
            line_text = _escape_ass(phrase)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Main,,0,0,0,,{line_text}"
        )
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_srt(phrases: list[str], timings: list[tuple[float, float]], path: Path) -> Path:
    lines = []
    for i, ((start, end), phrase) in enumerate(zip(timings, phrases), 1):
        lines.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{phrase}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path