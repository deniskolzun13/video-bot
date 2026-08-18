import asyncio
import logging
import re
import subprocess
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import httpx
from deep_translator import GoogleTranslator

import config
from tts import probe_duration

logger = logging.getLogger(__name__)

STOPWORDS = set(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже
    или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они
    тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под
    будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем
    чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
    над больше тот через эти нас про всего них какая много разве три эту моя впрочем свою
    the a an and or of to in is are was were for on with at by from as into about over under
    it its this that these those be been being not no so but if then than too very just
    also can could would should will shall may might must do does did have has had
    """.split()
)


@dataclass
class VideoClip:
    id: str
    url: str
    width: int
    height: int
    duration: float
    query: str


class VideoSourceProvider(ABC):
    @abstractmethod
    async def search(self, query: str, per_page: int = 5) -> list[VideoClip]:
        ...

    async def download(self, clip: VideoClip, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("GET", clip.url, follow_redirects=True) as response:
                response.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        return dest


class PexelsProvider(VideoSourceProvider):
    BASE_URL = "https://api.pexels.com/videos/search"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Задай PEXELS_API_KEY в .env")
        self.api_key = api_key

    async def search(self, query: str, per_page: int = 5) -> list[VideoClip]:
        params = {"query": query, "orientation": "portrait", "per_page": per_page}
        headers = {"Authorization": self.api_key}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(self.BASE_URL, params=params, headers=headers)
        if response.status_code != 200:
            raise ValueError(f"Pexels вернул ошибку {response.status_code}: {response.text[:300]}")
        data = response.json()
        clips: list[VideoClip] = []
        for video in data.get("videos", []):
            files = [
                f for f in video.get("video_files", [])
                if f.get("file_type") == "video/mp4" and f.get("link")
            ]
            if not files:
                continue
            chosen = None
            for f in sorted(files, key=lambda f: f.get("width") or 0, reverse=True):
                if (f.get("width") or 0) <= 1100 and (f.get("width") or 0) >= 480:
                    chosen = f
                    break
            if chosen is None:
                chosen = files[0]
            clips.append(
                VideoClip(
                    id=str(video["id"]),
                    url=chosen["link"],
                    width=chosen.get("width") or video.get("width") or 0,
                    height=chosen.get("height") or video.get("height") or 0,
                    duration=video.get("duration") or 0.0,
                    query=query,
                )
            )
        return clips


def extract_keywords_heuristic(text: str, n: int = config.KEYWORDS_COUNT) -> list[str]:
    words = re.findall(r"[а-яёa-z][а-яёa-z-]{3,}", text.lower())
    words = [w for w in words if w not in STOPWORDS and not w.isdigit()]
    freq = Counter(words)
    return [word for word, _ in freq.most_common(n)]


async def _llm_complete(prompt: str, timeout: float = 60) -> str | None:
    if not config.LLM_API_KEY:
        return None
    auth = config.LLM_API_KEY if config.LLM_API_KEY.startswith("sk-") else f"Api-Key {config.LLM_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": auth},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                },
            )
        if response.status_code != 200:
            logger.warning("LLM-запрос не удался: %s", response.status_code)
            return None
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM-запрос не удался: %s", exc)
        return None


async def extract_keywords_llm(text: str, n: int = config.KEYWORDS_COUNT) -> list[str] | None:
    prompt = (
        f"Ты подбираешь стоковые видео для новости. Извлеки {n} КОНКРЕТНЫХ визуальных тем "
        f"для поиска на Pexels — предметы, сцены, места, людей за работой (например: "
        f"computer, server room, office, circuit board, programmer typing). "
        f"ЗАПРЕЩЕНО: абстрактные понятия и многозначные слова (model, technology, news). "
        f"Верни ТОЛЬКО слова через запятую, на английском, без нумерации.\n\n"
        f"{text[:2000]}"
    )
    content = await _llm_complete(prompt)
    if not content:
        return None
    keywords = [w.strip() for w in re.split(r"[,;]", content) if w.strip()]
    return keywords[:n]


async def extract_game_name(text: str) -> str | None:
    """Название игры из текста новости (для SteamProvider)."""
    prompt = (
        "Из текста игровой новости извлеки название игры, как оно указано в Steam. "
        "Ответь ТОЛЬКО названием игры, БЕЗ кавычек, скобок и пояснений. "
        "Если игры в тексте нет — ответь одним словом «нет».\n\n"
        f"{text[:2000]}"
    )
    content = await _llm_complete(prompt)
    if not content:
        return None
    lowered = content.lower().strip()
    if lowered in ("нет", "none", "-", "n/a", "не найдено", "нет игры"):
        return None
    clean = re.sub(r"[«»\"'()\[\]]", "", content).strip(" .!?")
    return clean or None


async def extract_keywords(text: str, n: int = config.KEYWORDS_COUNT) -> list[str]:
    """Извлекает n ключевых тем для всего текста (не по фразам)."""
    keywords = await extract_keywords_llm(text, n)
    if keywords:
        return keywords
    return extract_keywords_heuristic(text, n)


def _has_cyrillic(keywords: list[str]) -> bool:
    return any(re.search(r"[а-яё]", word, re.IGNORECASE) for word in keywords)


async def translate_keywords(keywords: list[str]) -> list[str]:
    """Перевод ключевых слов RU->EN перед поиском в Pexels
    (Pexels ищет только по-английски — главная причина нерелевантной выдачи)."""
    if not _has_cyrillic(keywords):
        return keywords
    try:
        translated = await asyncio.to_thread(
            GoogleTranslator(source="ru", target="en").translate,
            ", ".join(keywords),
        )
        result = [w.strip() for w in translated.split(",") if w.strip()]
        if len(result) == len(keywords):
            logger.info("Перевод ключевых слов: %s -> %s", keywords, result)
            return result
        logger.warning("Перевод вернул %d слов вместо %d: %s", len(result), len(keywords), result)
    except Exception as exc:
        logger.warning("Не удалось перевести ключевые слова (%s), использую как есть", exc)
    return keywords


class SteamProvider(VideoSourceProvider):
    """Официальные трейлеры игр из Steam Store (HLS, без ключа).
    Видео точно совпадает с игрой из новости."""

    SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
    DETAILS_URL = "https://store.steampowered.com/api/appdetails/"

    def __init__(self, game_name: str):
        if not game_name:
            raise ValueError("Не указано название игры")
        self.game_name = game_name

    async def search(self, query: str, per_page: int = 1) -> list[VideoClip]:
        headers = {"Accept": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(
                self.SEARCH_URL,
                params={"term": query, "l": "english", "cc": "us"},
                headers=headers,
            )
            if response.status_code == 429:
                await asyncio.sleep(2)
                response = await client.get(
                    self.SEARCH_URL,
                    params={"term": query, "l": "english", "cc": "us"},
                    headers=headers,
                )
            if response.status_code != 200:
                raise ValueError(f"Steam search вернул ошибку {response.status_code}")
            items = response.json().get("items") or []
            if not items:
                return []
            appid = items[0]["id"]
            logger.info("Steam: «%s» -> appid=%s (%s)", query, appid, items[0].get("name"))

            details = await client.get(
                self.DETAILS_URL,
                params={"appids": appid, "l": "english"},
                headers=headers,
            )
            if details.status_code == 429:
                await asyncio.sleep(2)
                details = await client.get(
                    self.DETAILS_URL,
                    params={"appids": appid, "l": "english"},
                    headers=headers,
                )
            if details.status_code != 200:
                raise ValueError(f"Steam appdetails вернул ошибку {details.status_code}")
            data = details.json().get(str(appid), {}).get("data") or {}
            movies = data.get("movies") or []
            if not movies:
                return []
            movie = self._pick_best_movie(movies)
            url = movie.get("hls_h264") or movie.get("dash_h264") or ""
            if not url:
                return []
            logger.info("Steam: выбран трейлер «%s»", movie.get("name") or movie.get("id"))
            return [VideoClip(id=str(movie["id"]), url=url, width=0, height=0,
                              duration=0.0, query=query)]

    @staticmethod
    def _pick_best_movie(movies: list[dict]) -> dict:
        """highlight-трейлеры приоритетнее; из названия предпочитаем геймплей/официальный,
        избегаем тизеров и тизер-трейлеров."""
        def score(m: dict) -> int:
            name = (m.get("name") or "").lower()
            s = 0
            if m.get("highlight"):
                s += 100
            if any(w in name for w in ("gameplay", "official trailer", "launch trailer")):
                s += 50
            if any(w in name for w in ("teaser", "teaser trailer", "dlc trailer")):
                s -= 30
            if any(w in name for w in ("update trailer", "patch", "hotfix")):
                s -= 20
            return s

        return max(movies, key=score)

    async def download(self, clip: VideoClip, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", clip.url, "-map", "0:v", "-c", "copy", str(dest)]
        result = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", clip.url, "-map", "0:v",
                   "-c:v", "libx264", "-preset", "veryfast", str(dest)]
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True
            )
            if result.returncode != 0:
                raise ValueError(f"Не удалось скачать трейлер из Steam: {result.stderr[-500:]}")
        return dest


async def prepare_clips(
    phrases: list[str],
    timings: list[tuple[float, float]],
    provider: VideoSourceProvider,
    work_dir: Path,
) -> list[tuple[Path, float, float]]:
    """Возвращает [(путь_к_видео, длительность_сегмента, сдвиг_внутри_видео)]."""
    if isinstance(provider, SteamProvider):
        return await _prepare_steam_clips(phrases, timings, provider, work_dir)
    return await _prepare_pexels_clips(phrases, timings, provider, work_dir)


async def _prepare_steam_clips(
    phrases: list[str],
    timings: list[tuple[float, float]],
    provider: SteamProvider,
    work_dir: Path,
) -> list[tuple[Path, float, float]]:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    candidates = await provider.search(provider.game_name)
    if not candidates:
        raise ValueError(f"По игре «{provider.game_name}» Steam не нашёл трейлер")
    trailer = work_dir / "steam_trailer.mp4"
    await provider.download(candidates[0], trailer)
    duration = await asyncio.to_thread(probe_duration, trailer)
    if duration <= 0:
        raise ValueError(f"Трейлер «{provider.game_name}» не удалось разобрать")
    logger.info("Steam: трейлер скачан (%.1f с), режу на %d сегментов", duration, len(timings))

    result: list[tuple[Path, float, float]] = []
    offset = 0.0
    for (start, end) in timings:
        seg = max(end - start, 2.0)
        result.append((trailer, seg, offset))
        offset = (offset + seg) % duration
    return result


async def _prepare_pexels_clips(
    phrases: list[str],
    timings: list[tuple[float, float]],
    provider: VideoSourceProvider,
    work_dir: Path,
) -> list[tuple[Path, float, float]]:
    """Для каждой фразы скачивает клип с Pexels на основе тематической релевантности."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Для коротких текстов (< 3 фраз) используем старую логику
    if len(phrases) < 3:
        return await _prepare_pexels_clips_legacy(phrases, timings, provider, work_dir)

    # 1. Извлекаем глобальные темы для всего текста
    keywords = await extract_keywords(" ".join(phrases))
    keywords = await translate_keywords(keywords)
    if not keywords:
        keywords = ["technology", "abstract"]
    logger.info("Глобальные темы: %s", keywords)

    # 2. Ищем клипы для каждой темы (кэшируем результаты)
    theme_clips = {}
    used_ids = set()
    for kw in keywords:
        try:
            candidates = await provider.search(kw, per_page=10)
            # Фильтруем уже использованные
            fresh = [c for c in candidates if c.id not in used_ids]
            if fresh:
                theme_clips[kw] = fresh
        except ValueError:
            continue

    if not theme_clips:
        # Fallback: ищем по первым ключевым словам
        for kw in keywords[:3]:
            try:
                candidates = await provider.search(kw, per_page=10)
                fresh = [c for c in candidates if c.id not in used_ids]
                if fresh:
                    theme_clips[kw] = fresh
                    break
            except ValueError:
                continue

    if not theme_clips:
        raise ValueError("Pexels не вернул ни одного подходящего клипа")

    # 3. Распределяем клипы по фразам на основе релевантности
    result = []
    for i, ((start, end), phrase) in enumerate(zip(timings, phrases)):
        need = max(end - start, 2.0)
        dest = work_dir / f"clip_{i:03d}.mp4"

        # Определяем наиболее релевантную тему для этой фразы
        phrase_lower = phrase.lower()
        best_theme = None
        best_score = 0

        for theme, clips in theme_clips.items():
            # Простая оценка релевантности: пересечение слов
            theme_words = set(re.findall(r"\w+", theme.lower()))
            phrase_words = set(re.findall(r"\w+", phrase.lower()))
            overlap = len(theme_words & phrase_words)
            if overlap > best_score:
                best_score = overlap
                best_theme = theme

        # Если не нашли пересечения, берём тему с наибольшим количеством клипов
        if best_theme is None:
            best_theme = max(theme_clips, key=lambda k: len(theme_clips[k]))

        clip = None
        for c in theme_clips[best_theme]:
            if c.id not in used_ids:
                clip = c
                break

        if clip is None:
            # Fallback: любой неиспользованный клип
            for theme_clips_list in theme_clips.values():
                for c in theme_clips_list:
                    if c.id not in used_ids:
                        clip = c
                        best_theme = [k for k, v in theme_clips.items() if c in v][0]
                        break
                if clip:
                    break

        if clip is None:
            raise ValueError("Не удалось найти свободный клип для фразы")

        used_ids.add(clip.id)
        try:
            await provider.download(clip, dest)
            logger.info("Клип %d/%d: тема=%s, запрос=%s, id=%s",
                        i + 1, len(phrases), best_theme, clip.query, clip.id)
            result.append((dest, need, 0.0))
        except Exception as exc:
            logger.warning("Не удалось скачать клип %s: %s", clip.url, exc)
            raise ValueError(f"Не удалось скачать видео по теме «{best_theme}»: {exc}")

    return result


async def _prepare_pexels_clips_legacy(
    phrases: list[str],
    timings: list[tuple[float, float]],
    provider: VideoSourceProvider,
    work_dir: Path,
) -> list[tuple[Path, float, float]]:
    """Старая логика round-robin для коротких текстов."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    keywords = await extract_keywords(" ".join(phrases))
    keywords = await translate_keywords(keywords)
    if not keywords:
        keywords = ["technology", "abstract"]
    logger.info("Ключевые слова (legacy): %s", keywords)

    result: list[tuple[Path, float, float]] = []
    used_ids: set[str] = set()
    for i, ((start, end), phrase) in enumerate(zip(timings, phrases)):
        need = max(end - start, 2.0)
        dest = work_dir / f"clip_{i:03d}.mp4"
        clip = None
        for offset in range(len(keywords)):
            keyword = keywords[(i + offset) % len(keywords)]
            try:
                candidates = await provider.search(keyword, per_page=10)
            except ValueError:
                continue
            for c in candidates:
                if c.id not in used_ids:
                    clip = c
                    break
            if clip:
                break
        if clip is None:
            raise ValueError(f"Не удалось найти подходящее видео (запросы: {keywords[:3]}…)")
        used_ids.add(clip.id)
        try:
            await provider.download(clip, dest)
            logger.info("Клип %d/%d: запрос=%s, id=%s", i + 1, len(phrases), clip.query, clip.id)
            result.append((dest, need, 0.0))
        except Exception as exc:
            logger.warning("Не удалось скачать клип %s: %s", clip.url, exc)
            raise ValueError(f"Не удалось скачать видео по запросу «{clip.query}»: {exc}")
    return result