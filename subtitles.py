import re
from pathlib import Path

import config

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


def build_timings(phrases: list[str], total_duration: float) -> list[tuple[float, float]]:
    """Пропорциональный тайминг по длине текста (упрощение вместо forced alignment)."""
    total_chars = sum(len(p) for p in phrases) or 1
    timings: list[tuple[float, float]] = []
    t = 0.0
    for phrase in phrases:
        dur = total_duration * len(phrase) / total_chars
        timings.append((t, t + dur))
        t += dur
    return timings


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


def generate_ass(phrases: list[str], timings: list[tuple[float, float]], path: Path) -> Path:
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
        f"Style: Main,{config.SUB_FONT},{config.SUB_FONTSIZE},{config.SUB_PRIMARY},"
        f"{config.SUB_PRIMARY},{config.SUB_OUTLINE_COLOR},{config.SUB_OUTLINE_COLOR},"
        f"1,0,0,0,100,100,0,0,1,{config.SUB_OUTLINE_WIDTH},{config.SUB_SHADOW},"
        f"2,60,60,{config.SUB_MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for (start, end), phrase in zip(timings, phrases):
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Main,,0,0,0,,{_escape_ass(phrase)}"
        )
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_srt(phrases: list[str], timings: list[tuple[float, float]], path: Path) -> Path:
    lines = []
    for i, ((start, end), phrase) in enumerate(zip(timings, phrases), 1):
        lines.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{phrase}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path