"""Unit tests для scene planner (парсинг JSON) и анализатора."""

from script.analyzer import _extract_json, _validate
from script.generator import ScriptGenerator
from script.scene_planner import Scene, ScenePlan, _parse_scenes, map_scenes_to_phrases


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
            '{"scenes": [{"phrase_indexes": [0], "visual": "gaming pc", '
            '"keywords": ["pc", "gamer"], "duration_hint": 4}]}'
        )
        scenes = _parse_scenes(content)
        assert len(scenes) == 1
        assert scenes[0].visual == "gaming pc"
        assert scenes[0].keywords == ["pc", "gamer"]
        assert scenes[0].phrase_indexes == [0]
        assert not hasattr(scenes[0], "text")  # text не хранится

    def test_parse_ignores_text_field(self):
        """Поле text от LLM должно игнорироваться (текст не меняем)."""
        content = (
            '{"scenes": [{"text": "ИЗМЕНЁННЫЙ ТЕКСТ", "phrase_indexes": [2], '
            '"visual": "server room", "keywords": ["server"], "duration_hint": 5}]}'
        )
        scenes = _parse_scenes(content)
        assert len(scenes) == 1
        assert scenes[0].visual == "server room"
        assert scenes[0].phrase_indexes == [2]
        assert not hasattr(scenes[0], "text")

    def test_parse_phrase_indexes_as_strings(self):
        content = '{"scenes": [{"phrase_indexes": ["0", "3"], "visual": "x", "keywords": []}]}'
        scenes = _parse_scenes(content)
        assert len(scenes) == 1
        assert scenes[0].phrase_indexes == [0, 3]

    def test_parse_duration_hint_clamped_by_validate(self):
        content = '{"scenes": [{"phrase_indexes": [0], "visual": "x", "keywords": [], "duration_hint": 999}]}'
        scenes = _parse_scenes(content)
        plan = ScenePlan(scenes=scenes)
        assert plan.validate() is True
        assert plan.scenes[0].duration_hint == 30.0

    def test_parse_skips_invalid(self):
        content = '{"scenes": [{"text": "", "visual": "x"}, "not_a_dict"]}'
        scenes = _parse_scenes(content)
        assert scenes == []

    def test_parse_empty(self):
        assert _parse_scenes("") == []
        assert _parse_scenes("no json here") == []

    def test_parse_skips_scenes_without_indexes(self):
        content = '{"scenes": [{"visual": "x"}, {"phrase_indexes": [], "visual": "y"}, {"phrase_indexes": [1], "visual": "z"}]}'
        scenes = _parse_scenes(content)
        assert len(scenes) == 1
        assert scenes[0].visual == "z"


class TestSceneMapping:
    def _scenes(self, indexes, visuals=None):
        return [
            Scene(visual=(visuals[i] if visuals else f"v{i}"), keywords=["k"], phrase_indexes=list(idxs))
            for i, idxs in enumerate(indexes)
        ]

    def test_scene_mapping_1_1(self):
        phrases = ["p0"]
        scenes = self._scenes([[0]])
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 1
        assert mapped[0].visual == "v0"

    def test_scene_mapping_10_10(self):
        phrases = [f"p{i}" for i in range(10)]
        scenes = self._scenes([[i] for i in range(10)])
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 10
        for i, s in enumerate(mapped):
            assert s.visual == f"v{i}"

    def test_scene_mapping_10_6(self):
        """6 сцен на 10 фраз — не привязанные получают сцену по кругу."""
        phrases = [f"p{i}" for i in range(10)]
        scenes = self._scenes([[0], [2], [4], [5], [7], [9]])
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 10
        assert mapped[0].visual == "v0"
        assert mapped[2].visual == "v1"
        assert mapped[4].visual == "v2"
        assert mapped[9].visual == "v5"
        # неявная p1 берёт первую сцену по кругу, p6 — третью (круговой обход)
        assert mapped[1].visual == "v0"
        assert mapped[6].visual == "v2"

    def test_scene_mapping_10_3(self):
        phrases = [f"p{i}" for i in range(10)]
        scenes = self._scenes([[1], [4], [8]])
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 10
        assert mapped[1].visual == "v0"
        assert mapped[4].visual == "v1"
        assert mapped[8].visual == "v2"

    def test_scene_mapping_3_10(self):
        """3 фразы, 10 сцен — лишние сцены не создают фраз."""
        phrases = ["p0", "p1", "p2"]
        scenes = self._scenes([[0], [1], [2]] + [[3], [4], [5], [6], [7], [8], [9]])
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 3
        assert mapped[0].visual == "v0"

    def test_scene_mapping_empty_phrases(self):
        assert map_scenes_to_phrases([], [Scene(visual="x")]) == []

    def test_scene_mapping_empty_scenes(self):
        assert map_scenes_to_phrases(["p0", "p1"], []) == []

    def test_scene_mapping_invalid_indexes(self):
        """Индексы вне диапазона игнорируются; сцена без валидных индексов не мапится."""
        phrases = ["p0", "p1"]
        scenes = [
            Scene(visual="bad", phrase_indexes=[99, -1]),
            Scene(visual="good", phrase_indexes=[0]),
        ]
        mapped = map_scenes_to_phrases(phrases, scenes)
        assert len(mapped) == 2
        assert mapped[0].visual == "good"
        assert mapped[1].visual == "bad" or mapped[1].visual == "good"


class TestScenePlanValidation:
    def test_validate_accepts_valid(self):
        plan = ScenePlan(scenes=[Scene(visual="x", keywords=["k"], phrase_indexes=[0])])
        assert plan.validate() is True
        assert len(plan.scenes) == 1

    def test_validate_rejects_empty_phrase_indexes(self):
        plan = ScenePlan(scenes=[Scene(visual="x", phrase_indexes=[])])
        assert plan.validate() is False
        assert plan.scenes == []

    def test_validate_rejects_missing_visual(self):
        plan = ScenePlan(scenes=[Scene(visual="", phrase_indexes=[0])])
        assert plan.validate() is False

    def test_validate_rejects_bad_indexes_type(self):
        plan = ScenePlan(scenes=[Scene(visual="x", phrase_indexes=["a"])])
        assert plan.validate() is False

    def test_validate_clamps_duration_hint(self):
        plan = ScenePlan(scenes=[Scene(visual="x", phrase_indexes=[0], duration_hint=999)])
        assert plan.validate() is True
        assert plan.scenes[0].duration_hint == 30.0
        plan2 = ScenePlan(scenes=[Scene(visual="x", phrase_indexes=[0], duration_hint=0.1)])
        plan2.validate()
        assert plan2.scenes[0].duration_hint == 0.5

    def test_validate_drops_invalid_keeps_valid(self):
        plan = ScenePlan(scenes=[
            Scene(visual="bad", phrase_indexes=[]),
            Scene(visual="good", phrase_indexes=[1]),
        ])
        assert plan.validate() is True
        assert len(plan.scenes) == 1
        assert plan.scenes[0].visual == "good"


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