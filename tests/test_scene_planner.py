"""Unit tests для scene planner (парсинг JSON) и анализатора."""

from script.analyzer import _extract_json, _validate
from script.generator import ScriptGenerator
from script.scene_planner import _parse_scenes


class TestAnalyzerJsonParsing:
    def test_extract_json_plain(self):
        content = '{"topic": "Игры", "visual_keywords": ["gaming pc"]}'
        assert _extract_json(content)["topic"] == "Игры"

    def test_extract_json_with_markdown_wrapper(self):
        content = '```json\n{"topic": "Игры"}\n```'
        assert _extract_json(content)["topic"] == "Игры"

    def test_extract_json_with_garbage(self):
        content = 'Вот ответ: {"topic": "Игры", "entities": ["Valve"]} Спасибо!'
        data = _extract_json(content)
        assert data["topic"] == "Игры"

    def test_extract_json_invalid(self):
        assert _extract_json("просто текст без json") is None

    def test_validate_fills_defaults(self):
        a = _validate({"topic": "T", "title": "Title", "visual_keywords": ["pc"]})
        assert a is not None
        assert a.topic == "T"
        assert a.title == "Title"
        assert a.visual_keywords == ["pc"]
        assert a.entities == []

    def test_validate_rejects_garbage(self):
        assert _validate("not a dict") is None
        assert _validate(None) is None


class TestScenePlannerParsing:
    def test_parse_valid_scenes(self):
        content = (
            '{"scenes": [{"text": "Первая фраза.", "visual": "gaming pc", '
            '"keywords": ["pc", "gamer"], "duration_hint": 4}]}'
        )
        scenes = _parse_scenes(content, ["Первая фраза."])
        assert len(scenes) == 1
        assert scenes[0].text == "Первая фраза."
        assert scenes[0].visual == "gaming pc"
        assert scenes[0].keywords == ["pc", "gamer"]

    def test_parse_skips_invalid(self):
        content = '{"scenes": [{"text": "", "visual": "x"}, "not_a_dict"]}'
        scenes = _parse_scenes(content, [])
        assert scenes == []

    def test_parse_empty(self):
        assert _parse_scenes("", []) == []
        assert _parse_scenes("no json here", []) == []


class TestScriptGenerator:
    def test_extract_json(self):
        gen = ScriptGenerator.__new__(ScriptGenerator)  # без провайдера
        data = gen._extract_json('{"hook": "Х", "body": "Т", "ending": "Е"}')
        assert data == {"hook": "Х", "body": "Т", "ending": "Е"}

    def test_extract_json_with_wrapper(self):
        gen = ScriptGenerator.__new__(ScriptGenerator)
        data = gen._extract_json('```json\n{"hook": "Х"}\n```')
        assert data["hook"] == "Х"

    def test_extract_json_bad(self):
        gen = ScriptGenerator.__new__(ScriptGenerator)
        assert gen._extract_json("не json") is None