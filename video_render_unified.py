"""Unified FFmpeg render (разделы 19, 20, 22, 33): НЕСКОЛЬКО новостей -> ОДИН MP4.

Принимает UnifiedTimeline (сегменты intro/news/transition/outro с audio_path
и video_paths), строит ОДИН ffmpeg-filter_complex:
  - каждый сегмент: видео (клипы с loop/scale/crop) + свой audio (с смещением);
  - crossfade/fade между сегментами (TRANSITION_TYPE, TRANSITION_DURATION);
  - общие ASS-субтитры с абсолютными таймингами (offset уже в таймлайне);
  - title card перед новостью (NEWS_TITLE_DURATION) — текст заголовка поверх.

Выход — ОДИН mp4. Провалидируется ffprobe (video/audio/duration/resolution).
"""
import logging
import subprocess
from pathlib import Path

import config
from news.models import ITEM_NEWS, UnifiedTimeline
from subtitles import split_into_phrases
from video_render import _detect_h264_encoder, _escape_filter_path, validate_output

logger = logging.getLogger(__name__)


def _escape_ass(text: str) -> str:
    """Экранирование текста для ASS (запятая, скобки, переводы строк)."""
    return (text or "").replace("\\", "\\\\").replace("\n", "\\N").replace(",", "，")


class TimelineRenderError(ValueError):
    """Ошибка рендера unified timeline."""


def _ass_for_timeline(
    timeline: UnifiedTimeline,
    ass_path: Path,
    news_titles: dict[int, str],
) -> Path:
    """Генерирует общий ASS с абсолютными таймингами.

    Каждый сегмент: его текст (озвучка) в период [start, end].
    Title card новости: заголовок в первые NEWS_TITLE_DURATION секунд сегмента.
    """
    lines: list[str] = []

    def _ass_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int(seconds % 3600 // 60)
        s = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        if cs == 100:
            cs = 0
            s += 1
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    for item in timeline.items:
        # Title card: большая надпись в начале news-сегмента
        if item.type == ITEM_NEWS and config.NEWS_TITLE_ENABLED:
            title = news_titles.get(item.news_id or -1) or item.text[:60]
            title_end = min(item.start + config.NEWS_TITLE_DURATION, item.end)
            if title_end > item.start + 0.1:
                lines.append(
                    "Dialogue: 0,{start},{end},Title,,0,0,0,,{text}".format(
                        start=_ass_time(item.start),
                        end=_ass_time(title_end),
                        text=_escape_ass(title),
                    )
                )
        # Основной текст сегмента (озвучка) — с конца title card
        sub_start = item.start
        if item.type == ITEM_NEWS and config.NEWS_TITLE_ENABLED:
            sub_start = min(item.start + config.NEWS_TITLE_DURATION, item.end)
        if item.text and item.end - sub_start > 0.1:
            phrases = split_into_phrases(item.text)
            if phrases:
                # word-level тайминги (относительные) + offset таймлайна
                rel = None
                if item.phrase_timings and len(item.phrase_timings) == len(phrases):
                    rel = item.phrase_timings
                if rel is None:
                    # Пропорционально распределяем фразы по времени сегмента
                    seg_dur = item.end - sub_start
                    total_chars = sum(len(p) for p in phrases) or 1
                    rel = []
                    t = 0.0
                    for phrase in phrases:
                        dur = seg_dur * len(phrase) / total_chars
                        rel.append((t, t + dur))
                        t += dur
                for phrase, (p_start, p_end) in zip(phrases, rel):
                    abs_start = sub_start + p_start
                    abs_end = min(sub_start + p_end, item.end)
                    if abs_end - abs_start < 0.3:
                        abs_end = abs_start + 0.3
                    lines.append(
                        "Dialogue: 0,{start},{end},Main,,0,0,0,,{text}".format(
                            start=_ass_time(abs_start),
                            end=_ass_time(min(abs_end, item.end)),
                            text=_escape_ass(phrase),
                        )
                    )

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
        f"Style: Main,Arial,64,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,"
        f"1,5,2,2,40,40,100,1\n"
        f"Style: Title,Arial,110,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,"
        f"1,6,3,5,60,60,80,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return ass_path


def render_unified_timeline(
    timeline: UnifiedTimeline,
    ass_path: Path,
    out_path: Path,
    news_titles: dict[int, str] | None = None,
    cancel_token=None,
    width: int | None = None,
    height: int | None = None,
    transition_type: str = "",
    transition_duration: float = 0,
) -> Path:
    """Рендер всего таймлайна в ОДИН mp4.

    Для каждого сегмента ожидается item.audio_path (готовое TTS-аудио) и
    item.video_paths (клипы [(путь, duration, offset)]). Если video_paths пуст —
    сегмент получает сгенерированный фон (gradient/photo).

    Реализация: каждый сегмент рендерится в отдельный временный mp4 (видео+аудио
    с локальными таймингами), затем все сегменты склеиваются через concat demuxer
    (без повторного кодирования видео) и к ним применяется ass-субтитры
    (финальный проход с фильтром ass). Это гарантирует: никакого news1.mp4
    в output — все промежуточные файлы временные в work.
    """

    if cancel_token:
        cancel_token.check()

    out_path = Path(out_path)
    width = width or config.VIDEO_WIDTH
    height = height or config.VIDEO_HEIGHT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    transition_duration = transition_duration or config.TRANSITION_DURATION
    transition_type = (transition_type or config.TRANSITION_TYPE).lower()
    news_titles = news_titles or {}

    if not timeline.items:
        raise TimelineRenderError("Timeline пуст")

    # 1. Генерируем общий ASS с абсолютными таймингами
    _ass_for_timeline(timeline, ass_path, news_titles)

    # 2. Каждый сегмент -> отдельный временный mp4
    work_dir = out_path.parent / "_segments"
    work_dir.mkdir(parents=True, exist_ok=True)
    segment_files: list[Path] = []
    try:
        encoder = _detect_h264_encoder()
        for i, item in enumerate(timeline.items):
            if cancel_token:
                cancel_token.check()
            seg_out = work_dir / f"seg_{i:03d}.mp4"
            _render_segment(item, seg_out, width, height, encoder, cancel_token)
            segment_files.append(seg_out)

        # 3. Финальный проход: crossfade/fade между сегментами + ASS hardsub.
        #    Используем xfade (видео) и acrossfade (аудио) между всеми парами
        #    сегментов — настоящий переход, а не простая склейка.
        _render_assembled(
            segment_files,
            ass_path,
            out_path,
            encoder,
            transition_type=transition_type,
            transition_duration=transition_duration,
            cancel_token=cancel_token,
        )

        check = validate_output(out_path, target_w=width, target_h=height)
        if not check["ok"]:
            raise TimelineRenderError(
                "Валидация не пройдена: " + ", ".join(check["reasons"])
            )
        logger.info("Unified render OK: %s (%.1f с, %s)", out_path.name,
                    check["duration"], check["resolution"])
        return out_path
    finally:
        # Удаляем временные сегменты (но не out_path)
        try:
            for f in segment_files:
                f.unlink(missing_ok=True)
        except Exception:
            pass


def _render_assembled(
    segment_files: list[Path],
    ass_path: Path,
    out_path: Path,
    encoder: str,
    transition_type: str = "crossfade",
    transition_duration: float = 0.5,
    cancel_token=None,
) -> None:
    """Собирает сегменты в один MP4 с переходами (crossfade/fade) и ASS.

    - crossfade: xfade (видео) + acrossfade (аудио) между парами сегментов.
    - fade:      fade-in/out на стыках каждого сегмента (через concat).
    Если сегмент один — просто финальный проход с субтитрами.
    """

    out_path = Path(out_path)
    ass_path = Path(ass_path)
    transition_duration = max(transition_duration, 0.1)

    if len(segment_files) == 1:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(segment_files[0]),
            "-vf", f"ass={_escape_filter_path(ass_path)}",
            "-c:v", encoder, "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(config.FPS),
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(out_path),
        ]
        _run_ffmpeg(cmd, cancel_token, "финальный рендер")
        return

    inputs: list[str] = []
    for f in segment_files:
        inputs += ["-i", str(f)]

    vfilters: list[str] = []
    afilters: list[str] = []

    if transition_type == "crossfade":
        # xfade chain: offset_k = sum(durations[0..k]) - k*transition_duration
        # (offset — момент на таймлайне накопленного результата).
        offsets: list[str] = []
        cum = 0.0
        for k in range(len(segment_files) - 1):
            cum += _segment_dur(segment_files[k])
            offsets.append(str(round(cum - k * transition_duration, 3)))
        prev_v = "[0:v]"
        prev_a = "[0:a]"
        for i in range(1, len(segment_files)):
            idx = i - 1
            vfilters.append(
                f"{prev_v}[{i}:v]xfade=transition=fade:"
                f"duration={transition_duration:.3f}:offset={offsets[idx]}[vx{i}]"
            )
            afilters.append(
                f"{prev_a}[{i}:a]acrossfade=d={transition_duration:.3f}[ax{i}]"
            )
            prev_v, prev_a = f"[vx{i}]", f"[ax{i}]"
        v_out, a_out = prev_v, prev_a
    else:
        # fade: лёгкие fade-in/out на каждом сегменте, склейка без crossfade
        for i in range(len(segment_files)):
            dur = _segment_dur(segment_files[i])
            fade_in = min(transition_duration, dur / 2)
            fade_out = min(transition_duration, dur / 2)
            vfilters.append(
                f"[{i}:v]fade=t=in:st=0:d={fade_in:.3f},"
                f"fade=t=out:st={dur - fade_out:.3f}:d={fade_out:.3f}[fv{i}]"
            )
            afilters.append(f"[{i}:a]afade=t=in:d={fade_in:.3f},afade=t=out:st={dur - fade_out:.3f}:d={fade_out:.3f}[fa{i}]")
        concat_v = "".join(f"[fv{i}]" for i in range(len(segment_files)))
        concat_a = "".join(f"[fa{i}]" for i in range(len(segment_files)))
        vfilters.append(f"{concat_v}concat=n={len(segment_files)}:v=1:a=0[vout]")
        afilters.append(f"{concat_a}concat=n={len(segment_files)}:v=0:a=1[aout]")
        v_out, a_out = "[vout]", "[aout]"

    vfilters.append(f"{v_out}ass={_escape_filter_path(ass_path)}[vfinal]")

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex",
        ";".join(vfilters + afilters),
        "-map", "[vfinal]",
        "-map", a_out,
        "-c:v", encoder, "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(config.FPS),
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(out_path),
    ]
    _run_ffmpeg(cmd, cancel_token, "финальный рендер с переходами")


def _segment_dur(path: Path) -> float:
    """Длительность сегмента через ffprobe (для xfade offsets)."""
    try:
        import json
        import subprocess

        info = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True,
        )
        return float(json.loads(info.stdout)["format"]["duration"])
    except Exception:
        return 1.0


def _run_ffmpeg(cmd: list, cancel_token, label: str) -> None:
    """Запускает ffmpeg с таймаутом и обработкой отмены."""
    from utils.cancellation import CancellationError

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = proc.communicate(timeout=config.RENDER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise TimelineRenderError(
            f"Рендер занял больше {config.RENDER_TIMEOUT_SECONDS:.0f} с — прервано"
        )
    if cancel_token and cancel_token.is_cancelled:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise CancellationError(cancel_token.reason)
    if proc.returncode != 0:
        raise TimelineRenderError(
            f"Ошибка {label}: {(stderr or '')[-500:]}"
        )


def _render_segment(item, seg_out: Path, width: int, height: int, encoder: str, cancel_token) -> None:
    """Рендерит один сегмент таймлайна в mp4 (видео + его аудио).

    Видео: клипы из item.video_paths (loop, scale, crop, trim). Если пусто —
    генерируется градиентный фон.
    Аудио: item.audio_path (TTS), приводится к длительности сегмента.
    """
    from utils.cancellation import CancellationError

    if cancel_token:
        cancel_token.check()
    seg_out.parent.mkdir(parents=True, exist_ok=True)
    duration = max(item.duration, 0.1)

    inputs = list(item.video_paths or [])
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if inputs:
        for clip_path, _seg_dur, _start in inputs:
            cmd += ["-stream_loop", "-1", "-i", str(clip_path)]
    cmd += ["-i", str(item.audio_path)]

    filters: list[str] = []
    video_labels: list[str] = []

    if inputs:
        # Распределяем длительность: последний клип покрывает остаток,
        # чтобы сумма сегментов видео == длительности сегмента.
        planned = [max(sd, 0.5) for _, sd, _ in inputs]
        gap = duration - sum(planned)
        if gap > 0:
            planned[-1] = planned[-1] + gap
        for i, ((clip_path, _seg_dur, start), seg_dur) in enumerate(zip(inputs, planned)):
            if config.VIDEO_PADDING == "blur":
                chain = (
                    f"[{i}:v]split[b{i}][f{i}];"
                    f"[b{i}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},boxblur=20:5[bg{i}];"
                    f"[f{i}]scale={width}:{height}:force_original_aspect_ratio=decrease[fg{i}];"
                    f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,"
                    f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS[v{i}]"
                )
            else:
                chain = (
                    f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},setsar=1,"
                    f"trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS[v{i}]"
                )
            filters.append(chain)
            video_labels.append(f"[v{i}]")
    else:
        # Фон (gradient)
        filters.append(
            f"color=c=0x1a1a2e:s={width}x{height}:d={duration:.2f},format=yuv420p[v0]"
        )
        video_labels.append("[v0]")

    concat_in = "".join(video_labels)
    if len(video_labels) > 1:
        filters.append(f"{concat_in}concat=n={len(video_labels)}:v=1:a=0[vc]")
        v_out = "[vc]"
    else:
        v_out = video_labels[0]

    audio_index = len(inputs)  # индекс аудио-входа (после клипов)
    filters.append(
        f"[{audio_index}:a]apad=pad_dur=0.2,atrim=0:{duration:.2f},asetpts=PTS-STARTPTS[aout]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", v_out,
        "-map", "[aout]",
        "-c:v", encoder,
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-r", str(config.FPS),
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration:.2f}",
        str(seg_out),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = proc.communicate(timeout=config.RENDER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise TimelineRenderError("Рендер сегмента превысил таймаут")
    if cancel_token and cancel_token.is_cancelled:
        proc.terminate()
        try:
            proc.kill()
        except Exception:
            pass
        raise CancellationError(cancel_token.reason)
    if proc.returncode != 0:
        raise TimelineRenderError(
            f"Ошибка рендера сегмента {item.id}: {(stderr or '')[-500:]}"
        )


__all__ = ["render_unified_timeline", "TimelineRenderError", "_ass_for_timeline"]