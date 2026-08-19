"""Пресеты стилей субтитров.

Минимум: максимум 2 строки, безопасная зона, крупный шрифт, читаемость,
outline, shadow, автоперенос. Выбор через config.SUBTITLE_STYLE.
"""
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class SubtitleStyle:
    name: str
    fontname: str
    fontsize: int
    primary: str        # ASS BGR hex, напр. &H00FFFFFF
    secondary: str
    outline: str
    outline_width: float
    shadow: float
    margin_v: int
    bold: int = 1
    alignment: int = 2  # 2 = снизу по центру
    margin_l: int = 60
    margin_r: int = 60
    karaoke: bool = False
    highlight: bool = False
    highlight_color: str = "&H00FF00&"


def _style_from_env(override: dict) -> SubtitleStyle:
    """Собирает стиль из .env-переменных (для тонкой настройки поверх пресета)."""
    return SubtitleStyle(
        name=override.get("name", "Main"),
        fontname=override.get("fontname", config.SUB_FONT),
        fontsize=override.get("fontsize", config.SUB_FONTSIZE),
        primary=override.get("primary", config.SUB_PRIMARY),
        secondary=override.get("secondary", config.SUB_PRIMARY),
        outline=override.get("outline", config.SUB_OUTLINE_COLOR),
        outline_width=override.get("outline_width", config.SUB_OUTLINE_WIDTH),
        shadow=override.get("shadow", config.SUB_SHADOW),
        margin_v=override.get("margin_v", config.SUB_MARGIN_V),
        bold=override.get("bold", 1),
        alignment=override.get("alignment", 2),
        margin_l=override.get("margin_l", config.SUB_MARGIN_V),
        margin_r=override.get("margin_r", config.SUB_MARGIN_V),
        karaoke=override.get("karaoke", config.SUB_KARAOKE),
        highlight=override.get("highlight", config.SUB_HIGHLIGHT_KEYWORDS),
        highlight_color=override.get("highlight_color", config.SUB_HIGHLIGHT_COLOR),
    )


PRESETS: dict[str, SubtitleStyle] = {
    "classic": _style_from_env({
        "fontsize": 60, "outline_width": 4, "shadow": 2, "bold": 1,
        "primary": "&H00FFFFFF", "outline": "&H00000000",
        "margin_v": 90, "alignment": 2,
    }),
    "tiktok": _style_from_env({
        "fontsize": 72, "outline_width": 5, "shadow": 3, "bold": 1,
        "primary": "&H00FFFFFF", "outline": "&H00121212",
        "margin_v": 100, "alignment": 2, "margin_l": 40, "margin_r": 40,
    }),
    "news": _style_from_env({
        "fontsize": 58, "outline_width": 3, "shadow": 1, "bold": 1,
        "primary": "&H00FFFFFF", "outline": "&H00000000",
        "margin_v": 80, "alignment": 2,
    }),
    "gaming": _style_from_env({
        "fontsize": 66, "outline_width": 6, "shadow": 4, "bold": 1,
        "primary": "&H0000FFFF", "outline": "&H00000000",  # жёлтый, геймерский
        "margin_v": 100, "alignment": 2,
        "highlight": True, "highlight_color": "&H00FF00&",
    }),
    "minimal": _style_from_env({
        "fontsize": 64, "outline_width": 2, "shadow": 0, "bold": 0,
        "primary": "&H00FFFFFF", "outline": "&H00101010",
        "margin_v": 90, "alignment": 2,
    }),
}


def get_subtitle_style(name: str | None = None) -> SubtitleStyle:
    """Возвращает стиль по имени (или config.SUBTITLE_STYLE по умолчанию)."""
    name = (name or getattr(config, "SUBTITLE_STYLE", "tiktok")).lower()
    return PRESETS.get(name, PRESETS["tiktok"])